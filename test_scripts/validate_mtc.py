import sys
import mido

from dftt_timecode import DfttTimecode
from fractions import Fraction

from time import time_ns

inport = None

#print(mido.get_input_names())

for port in mido.get_input_names():
    if port[:4]=='PICO':
        inport = port
        print("Using:", inport)
        break

if inport == None:
    sys.exit("Unable to find PICO")

miss = -1
packet = [None]*8
packets = 0
last = None

fps = [24, 25, 29.97, 30]
last_t = 0

with mido.open_input(inport) as port:
    for message in port:
        if message.type=='sysex':
            print(message)

            # reset
            miss = -1
            last = None

            # sysex data=(127,127,1,1,65,0,9,15) time=0
            packet = [
                    message.data[7] & 0x0F,
                    message.data[7] >> 4,
                    message.data[6] & 0x0F,
                    message.data[6] >> 4,
                    message.data[5] & 0x0F,
                    message.data[5] >> 4,
                    message.data[4] & 0x0F,
                    message.data[4] >> 4
                    ]
            print(packet)
            packets = 16


        if message.type=='quarter_frame':
            if miss < 0:
                miss = message.frame_type
            else:
                miss += 1
                if miss > 7:
                    miss = 0
                if miss != message.frame_type:
                    print("missed quarter packet", miss, message.frame_type)
                    miss = message.frame_type

            packet[message.frame_type] = message.frame_value
            packets += 1

            # Once we have a few stored up....
            if packets > 16:
                #if message.frame_type == 0 or message.frame_type == 4:
                # simplify code by printing later in LTC frame, on 4th quarter frame
                if message.frame_type == 3 or message.frame_type == 7:
                    hh = ((packet[7] & 1) << 4) + packet[6]
                    mm = (packet[5] << 4) + packet[4]
                    ss = (packet[3] << 4) + packet[2]
                    ff = (packet[1] << 4) + packet[0]

                    lim = fps[packet[7] >> 1]

                    # need to evaluate the minute rollover(s), which may be drop-frame
                    if message.frame_type == 3 and last:
                        if int(last.timestamp) != int((last+1).timestamp):
                            print("frame rollover")
                            if ss == 0:
                                mm += 1
                                if mm >= 60:
                                    mm = 0
                                    hh += 1
                                    if hh >= 24:
                                        hh = 0
                    '''
                    if message.frame_type == 7:
                        ff += 1
                        if ff >= int(lim + 0.5):
                            ff = 0
                            ss += 1
                            if ss >= 60:
                                ss = 0
                                # don't need to propergate further as the sent hh and mm should be correct
                    '''

                    if lim == 29.97:
                        try:
                            tc = DfttTimecode("%2.2d:%2.2d:%2.2d;%2.2d" % (hh, mm, ss, ff),
                                    fps=Fraction(30000/1001),
                                    drop_frame = True)
                        except:
                            tc = None
                    else:
                        try:
                            tc = DfttTimecode("%2.2d:%2.2d:%2.2d:%2.2d" % (hh, mm, ss, ff),
                                    fps=lim,
                                    drop_frame = False)
                        except:
                            tc = None
                    if tc:
                        if message.frame_type == 7:
                            tc += 1
                        t = time_ns()
                        print(tc, message.frame_type, t - last_t, tc.timestamp)
                        last_t = t
                    else:
                        print("\nDfttTimecode Error:")
                        print("%d = %2.2d:%2.2d:%2.2d:%2.2d" % (message.frame_type, hh, mm, ss, ff))
                        print("fps =", lim)
                        for i in range(8):
                            print("\t0xF0 0x%2.2x" % ((i<<4) + packet[i]))
                        exit()

                    # santity check
                    if last and (last + 1) != tc:
                        print("TC glitched:")
                        print("%d = %2.2d:%2.2d:%2.2d:%2.2d" % (message.frame_type, hh, mm, ss, ff))
                        for i in range(8):
                            print("\t0xF0 0x%2.2x" % ((i<<4) + packet[i]))

                        exit(0)

                    last = tc


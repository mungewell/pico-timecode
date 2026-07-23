
from libs import config

# set 'XTAL freq' to compute what the calibrations should be...
freq = 0

# optimal divider computed for CPU clock at 180MHz
xtal = 12_000_000
optimal = [
        [30.00, (2343 + (192/256))],        # new_div = 0x0927c000
        [29.97, (2346 + ( 24/256))],        # new_div = 0x092a1800
        [25.00, (2812 + (128/256))],        # new_div = 0x0afc8000
        [24.98, (2815 + ( 80/256))],        # new_div = 0x0aff5000
        [24.00, (2929 + (176/256))],        # new_div = 0x0b71b000
        [23.98, (2932 + (158/256))],        # new_div = 0x0b749e00
    ]

def find_ideal(fps):
    ideal = 0
    for i in range(len(optimal)):
        if optimal[i][0] == fps:
            ideal = optimal[i][1]
            break

    return ideal

def find_freq(cal, ideal):
    frac = abs(cal-int(cal))
    #print("Fractional", frac)

    if cal >= 0:
        cdiv = ideal - (abs(int(cal)/256) * (1-frac)) - (abs(int(cal+1)/256) * frac)
        cfreq = xtal * cdiv / ideal
    else:
        # (1562 + (128/256)) + ( ((8/256) * (.390625)) + ((9/256) * (1-.390625)) )
        cdiv = ideal + (abs(int(cal)/256) * (1-frac)) + (abs(int(cal-1)/256) * frac)
        cfreq = xtal * cdiv / ideal

    print("Calc divider   :", cdiv)
    print("Calc XTAL freq :", cfreq)

    return cfreq

def find_cal(freq, fps):
    cal = 0.0

    if int(fps) != fps:
        # use true fps
        tfps = int(fps+1) / 1.001
    else:
        tfps = fps

    frame_freq = tfps * 80 * 32 # bits_in_frame and multipler
    cdiv = (180_000_000 / frame_freq) * (xtal/freq)

    #print("0x%8.8x" % ((int(cdiv * 256) << 8) & 0xFFFFFF00))
    cal = (cdiv - find_ideal(fps)) * 256
    return cal

#------------------------

# Check ID
try:
    print("Unit 'ub_name' :", config.userbits['ub_name'])
except:
    pass

# loop through all available calibrations, printing out details
freqs = []
for i in range(len(optimal)):
    print()

    setting = None
    check = str(optimal[i][0])
    print("Checking '%s' fps:" % check)
    try:
        setting = config.calibration[check]
    except:
        if int(optimal[i][0]) == optimal[i][0]:
            # Note: '30.0' may also be written '30' or '30.00'
            try:
                print("Checking '%s' fps:" % (check.split('.')[0]))
                setting = config.calibration[check.split('.')[0]]
            except:
                try:
                    print("Checking '%s' fps:" % (check + '0'))
                    setting = config.calibration[check + '0']
                except:
                    pass

    if setting:
        print("Calibration    :", setting)
        ideal = find_ideal(optimal[i][0])
        if ideal:
            print("Ideal divider  : %f (at %f fps)" % (ideal,optimal[i][0]))
            freqs.append(find_freq(float(setting), ideal))

if freq:
    # compute the calibrations to match this frequency, ~ 12,000,000MHz
    print("\n\nComputing calibrations for %f MHz:\n" % (freq / 1_000_000))

    print("calibration = {")
    for i in range(len(optimal)):
        ideal = find_ideal(optimal[i][0])

        if ideal:
            print("\t'%2.2f' : %f," % (optimal[i][0], find_cal(freq, optimal[i][0])))
    print("}")

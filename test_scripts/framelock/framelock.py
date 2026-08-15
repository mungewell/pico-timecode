#!/usr/bin/env python3

from dftt_timecode import DfttTimecode
from fractions import Fraction

from argparse import ArgumentParser
from datetime import datetime
import subprocess
import sys
import os

REF_FPS = None
REF_SR = None

def get_framerate(filepath):
    """Reads the framerate of a video file using ffprobe."""
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath)
    ]
    try:
        kwargs = {"capture_output": True, "text": True, "check": True, "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(command, **kwargs)
        fps_str = result.stdout.strip()
        if fps_str:
            parts = fps_str.split('/')
            if len(parts) == 2:
                return [int(parts[0]), int(parts[1])]
            else:
                return [int(fps_str), 1]
            return int(round(fps))
    except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
        pass
    return [None,None]

def get_samplerate(filepath):
    """Reads the samplerate of a video/audio file using ffprobe."""
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath)
    ]
    try:
        kwargs = {"capture_output": True, "text": True, "check": True, "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(command, **kwargs)
        fps_str = result.stdout.strip()
        if fps_str:
            return int(fps_str)
    except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
        pass
    return None

def get_ltcdump(filepath, channel=1, numerator=None, denominator=None):
    """Reads the first LTC frame of audio file using ltcdump."""
    if numerator:
        if denominator:
            command = [
                "ltcdump",
                "-f", str(numerator) + "/" + str(denominator),
                "-c", str(channel),
                str(filepath)
            ]
        else:
            command = [
                "ltcdump",
                "-f", str(numerator),
                "-c", str(channel),
                str(filepath)
            ]
    else:
        command = [
            "ltcdump",
            "-c", str(channel),
            str(filepath)
        ]
    try:
        kwargs = {
                "text": True,
                "stdout": subprocess.PIPE,
                }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(command, **kwargs)

        while True:
            ltc_str = process.stdout.readline().strip()
            if not ltc_str:
                break
            elif ltc_str[0] == "#":
                continue
            process.kill()

            return ltc_str
    except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
        pass
    return None


def main():
    global REF_FPS, REF_SR

    parser = ArgumentParser(prog="framelock")

    parser.add_argument('files', metavar='FILE', nargs='+',
        help='File(s) to process')

    parser.add_argument("-a", "--ach",
        type=int, default=1, dest="ach",
        help="LTC audio channel, for audio files")

    parser.add_argument("-v", "--vch",
        type=int, default=1, dest="vch",
        help="LTC audio channel, for video files")

    options = parser.parse_args()

    if not len(options.files):
        parser.error("FILE not specified")
    else:
        now = datetime.now()
        now_str = now.strftime("%m-%d-%Y_%H-%M-%S-%f")

        out_path="framelock_" + now_str

        # Using try/except as OSError may be raised due invalid path name etc
        try:
            # exist_ok=True suppresses the exception if folder already exists
            os.makedirs(out_path, exist_ok = True)
            print("Created directory: ./" + out_path + "/")
        except OSError as error:
            print("Error! Unable to create directory: ./" + out_path + "/")

    # itterate though list of files, 1st file is the reference
    for target in options.files:
        print("\nProcessing:", target)

        SR = get_samplerate(target)
        LTC = None

        if not SR:
            print("File does not appear to have audio track, skipping.")
            continue
        else:
            print("Audio samplerate", SR)
            if REF_SR and REF_SR != SR:
                print("Samplerate does not match reference, skipping")
                continue

            FPS = get_framerate(target)
            if FPS != [None, None]:
                print("Video FPS:", FPS)
                if REF_FPS and REF_FPS != FPS:
                    print("Framerate does not match reference, skipping")
                    continue

                # need to extract the audio portion as a '.wav'
                command = [
                    "ffmpeg",
                    "-v", "error", "-y",
                    "-i", str(target),
                    "-vn", "-c:a", "pcm_s16le",
                    "framelock.wav"
                ]
                try:
                    kwargs = {"stdin": subprocess.DEVNULL}
                    if sys.platform == "win32":
                        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    result = subprocess.run(command, **kwargs)

                    LTC = get_ltcdump("framelock.wav", options.vch, FPS[0], FPS[1])
                except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
                    pass
            else:
                # process audio file directly
                # do we need to itterate chanels?
                if REF_FPS:
                    LTC = get_ltcdump(target, options.ach, REF_FPS[0], REF_FPS[1])
                else:
                    LTC = get_ltcdump(target, options.ach)

            if LTC:
                    parts = LTC.split()
                    tc_str = list(parts[1])
                    sample_str = parts[3]

                    df = False
                    if tc_str[8] == '.':
                        df = True
                        tc_str[8] = ';'

                    tc = None
                    if REF_FPS:
                        '''
                        if FPS and FPS != REF_FPS:
                            print("Framerate does not match reference, skipping")
                            continue
                        '''

                        tc = DfttTimecode("".join(tc_str), \
                                fps=Fraction(REF_FPS[0], REF_FPS[1]), \
                                drop_frame = df)

                        print("\nFound LTC Packet:", tc, "@", sample_str)

                        # process current file to align properly...
                        continue
                    else:
                        # computing reference
                        if FPS and FPS[0] and FPS[1]:
                            tc = DfttTimecode("".join(tc_str), \
                                    fps=Fraction(FPS[0], FPS[1]), \
                                    drop_frame = df)

                    if tc:
                        print("\nReference:")
                        print("Found LTC Packet:", tc, "@", sample_str)
                        correction = int((int(sample_str) / SR) * tc.fps)

                        tc2 = tc - correction
                        print("Writing Start TC:", tc2)

                        # write TC meta-data to copy of file
                        command = [
                            "ffmpeg",
                            "-v", "error", "-y",
                            "-i", str(target),
                            "-map", "0", "-map_metadata", "0",
                            "-map_metadata:s:v", "0:s:v",
                            "-map_metadata:s:a", "0:s:a",
                            "-c", "copy",
                            "-timecode", str(tc2),
                            os.path.join(out_path, target)
                        ]
                        try:
                            kwargs = {"stdin": subprocess.DEVNULL}
                            if sys.platform == "win32":
                                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                            result = subprocess.run(command, **kwargs)

                            if FPS:
                                REF_FPS = FPS
                            REF_SR = SR

                        except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
                            pass




if __name__ == "__main__":
    main()


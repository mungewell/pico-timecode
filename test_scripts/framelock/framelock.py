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
REF_OFFSET = None

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
            return fps_str
    except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
        pass
    return None

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

def get_ltcdump(filepath, channel=1, fps=None):
    """Reads the first LTC frame of audio file using ltcdump."""
    if fps:
        command = [
            "ltcdump",
            "-f", str(fps),
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
    global REF_FPS, REF_SR, REF_OFFSET

    parser = ArgumentParser(prog="framelock")

    parser.add_argument('files', metavar='FILE', nargs='+',
        help='File(s) to process')

    parser.add_argument("-a", "--ach",
        type=int, default=1, dest="ach",
        help="LTC audio channel, for audio files")

    parser.add_argument("-v", "--vch",
        type=int, default=1, dest="vch",
        help="LTC audio channel, for video files")

    parser.add_argument("-c", "--correction",
        type=int, default=0, dest="correction",
        help="force correction for audio channel(s), ie move by +/- n samples")

    parser.add_argument("-n", "--no-ref",
        action="store_true", dest="noref",
        help="prevent correction of audio channel(s)")

    options = parser.parse_args()

    if not len(options.files):
        parser.error("FILE(s) not specified")
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
            exit()

    # itterate though list of files
    # 1st file is used as reference for others
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
            if FPS:
                print("Video FPS:", FPS)
                if REF_FPS and REF_FPS != FPS:
                    print("Framerate does not match reference, skipping")
                    continue

            # extract/re-encode the audio track(s) as a '.wav'
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

                # do we need to itterate chanels?
                if REF_FPS:
                    LTC = get_ltcdump("framelock.wav", options.ach, REF_FPS)
                else:
                    LTC = get_ltcdump("framelock.wav", options.ach)

            except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
                pass

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
                        parts = REF_FPS.split('/')
                    else:
                        parts = FPS.split('/')

                    if len(parts) > 1:
                        tc = DfttTimecode("".join(tc_str), \
                                fps=Fraction(int(parts[0]), int(parts[1])), \
                                drop_frame = df)
                    else:
                        tc = DfttTimecode("".join(tc_str), \
                                fps=int(parts[0]), \
                                drop_frame = df)

                    if tc:
                        print("Found LTC Packet:", tc, "@", sample_str)
                        frames = int((int(sample_str) / SR) * tc.fps)
                        OFFSET = int(sample_str) - int(frames * SR / tc.fps)

                        correction = 0
                        if options.correction:
                            correction = options.correction
                        elif REF_OFFSET:
                            correction = REF_OFFSET - OFFSET
                        if correction:
                            print("Correction:", correction)

                        tc2 = tc - frames
                        print("Writing Start TC:", tc2)

                        # build command to write a copy of file
                        command = [
                            "ffmpeg",
                            "-v", "error", "-y",
                            "-i", str(target),
                            "-map", "0", "-map_metadata", "0"
                        ]
                        if FPS:
                            # video
                            command += [
                                "-map_metadata:s:v", "0:s:v"
                            ]
                        command += [
                            "-map_metadata:s:a", "0:s:a"
                        ]

                        if correction > 0:
                            # have to re-encode in order to trim
                            command += [
                                "-af", "atrim=start_sample=" + \
                                str(correction) + ",apad=pad_len=" + \
                                str(correction)
                            ]
                        elif correction < 0:
                            command += [
                                "-af", "adelay=delays=" + \
                                str(0-correction) + "S:all=1"
                            ]
                            # leaves the extra samples at end..
                        else:
                            command += [
                                "-c", "copy",
                            ]

                        if FPS:
                            # video
                            command += [
                                "-timecode", str(tc2),
                            ]
                        else:
                            # audio, samples since midnight
                            command += [
                                "-write_bext", "1",
                                "-metadata", "time_reference="+str(tc2.time * SR)
                            ]

                        command += [
                            os.path.join(out_path, target)
                        ]
                        try:
                            kwargs = {"stdin": subprocess.DEVNULL}
                            if sys.platform == "win32":
                                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                            result = subprocess.run(command, **kwargs)

                            # store reference for processing other files
                            if not options.noref and not REF_SR:
                                print("File used as Reference.\n")
                                if FPS:
                                    REF_FPS = FPS
                                REF_SR = SR
                                REF_OFFSET = OFFSET

                        except (subprocess.CalledProcessError, ValueError, ZeroDivisionError):
                            pass

if __name__ == "__main__":
    main()


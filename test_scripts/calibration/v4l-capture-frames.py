# Grab 10 frames from a V4Linux capture card.
# I use this is record the visual information from Evertz 5300
#
# Requires:
# https://github.com/antmicro/pyrav4l2.git

from pyrav4l2 import Device, Stream

from sys import exit

dev = Device("/dev/video2")
print(f"Device name: {dev.device_name}")
print(f"Driver name: {dev.driver_name}")

if not dev.is_video_capture_capable:
    exit("Device does not support video capturing")

color_format, frame_size = dev.get_format()
print(f"Color format: {color_format}")
print(f"Frame size: {frame_size}")

'''
print("Available Formats:")
available_formats = dev.available_formats
for color_format in available_formats.keys():
    print(f"{color_format}:")
    for frame_size in available_formats[color_format]:
        print(f"    {frame_size}")
    print()

color_format = list(available_formats.keys())[0]
if available_formats[color_format]:
    frame_size = available_formats[color_format][0]
    dev.set_format(color_format, frame_size)

print("Available Controls:")
available_controls = dev.controls
for control in available_controls:
    print(control.name)
    dev.reset_control_to_default(control)
'''

# Grab 10 frames as save as individual files
for (j, frame) in enumerate(Stream(dev)):
    print(f"Frame {j}: {len(frame)} bytes")

    f = open("frame-%d.raw" % j, "wb")
    f.write(frame)

    # Then convert with something like:
    # $ ffmpeg -f rawvideo -s 720x480 -pix_fmt uyvy422 -i frame-1.raw frame-1.png

    # or use this viwer
    # https://github.com/antmicro/raviewer

    if j >= 9:
        break

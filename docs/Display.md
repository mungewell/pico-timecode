# Explaining the Display

If you've built a Pico-Timecode device, you might have a screen. At time of writing I am using the Pico-OLED-1.3 from Seed Studios, 
this is a nice screen but at $10 it's not the cheapest option - and is a little limited with just two buttons for the menu.

Norminally the screen will show important information all the time:
- Current TX Timecode, the big numbers across the bottom. Normally this will be counting, but the output may be manually paused or be waiting to 'Jam'.
- Current TX User-Bits, just above the Timecode. This may be 8x Digits, 4x Characters, or even a Date (with particular formating).
- Current TC Framerate, in top right. Drop Frame modes will also read 'DF'

![Example Display](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/first_board.jpg)

Updating the OLED (via SPI) is slow, my code is optimized and can display the count of every frame when only showing TX information. If the RX
monitor is enabled there is simply too much information to display, and the displayed TC's will jump (displaying roughly 4 frames in a second).

NOTE: The TC output is generated in the PIO hardware, it is NOT affected by CPU/Display slowdown. The LED is also controlled directly by hardware, 
it always blinks at the correct time.

## RX display

When the RX monitor is enabled, and when Jam'ing the display shows additional information:
- RX Mode, indicates Jam, RX monitor or Calibration.
- RX Timecode, as being sent by 'other' device. If it is not changing then that device is stopped/detached, or signal is not clear enough to decode
- RX User-Bits, again in either Digits, Characters or Date format.
- Info bargraph, which shows either the Jam status/progress, or represents a timing difference between RX and TX.

![Example Display with RX](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/PI-6_display.jpg)

NOTE: When the RX and TX timecodes may not match, as the display is too slow this information is _printed_ at different times. Even when the RX input and TX 
output are exactly aligned, the displays likely would show different values if you snap a picture... hopefully this can be resolved as code improves.

After Jam'ing there should be 3 ticks for the bargraph, the center represents exact timing and the left/right represet a difference of 1/2 frame fast/slow.
_There is also a Zoom mode where the bargraph is magnified to represent fast/slow to 1/20 frame, although most users don't need this. When Zoom'ed the 
left/right ticks are not shown._

The bargraph does NOT evaluate the Timecode value/contents, it is a measure of the relative timing of the signals. ie how the timing of start of the LTC 
frame aligns, this can be seen if you capture a recording of the output (with input looped through as well).

![Audio recording with LTC frames marked](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/PI-6_sample_audio.png)


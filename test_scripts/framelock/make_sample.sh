BASETC="01:00:00:00"
RATE="30000/1001"

# create 60s audio file, with randomly offset LTC (by upto 1s)
OFFSET=`awk -v min=0 -v max=1 'BEGIN{srand(); print min+rand()*(max-min+1)}'`
echo generating offset of $OFFSET

# mix LTC to one side of stero file
ltcgen -f $RATE"df" -s 48000 -t $BASETC -l 1:00:00 ltc_m.wav 2>&1 >> /dev/null
sox ltc_m.wav -c 2 ltc_s.wav remix 1 0
sox -n -r 48000 -c 2 silent.wav trim 0.0 $OFFSET
sox silent.wav ltc_s.wav audio.wav

# check the first reported TC, and make 'labels' for audacity
ltcdump -a audio.wav > audio.txt
TC=`ltcdump audio.wav | grep -v -e "^#" | head -n 1`
echo $TC

# render image to 60s 1080p video, with LTC audio track
# using SVG from:
# https://github.com/edent/SVGtestcard

if [[ -f "video_1s.mp4" ]]; then
    echo "1s exists."
else
    ffmpeg -y -loop 1 -r $RATE -i BBC-Test-Card-F.svg -c:v libx264 -video_size 1920x1080 -t 1 -pix_fmt yuv420p video_1s.mp4
fi

if [[ -f "video.mp4" ]]; then
    echo "video exists."
else
    ffmpeg -y -stream_loop 60 -i video_1s.mp4 -c copy -to 1:00 video.mp4 
fi

if [[ -f "sample.mp4" ]]; then
    echo "sample exists."
else
    ffmpeg -y -i video.mp4 -i audio.wav -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 sample.mp4
fi

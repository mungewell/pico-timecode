
# Create target directories
#for d in "ACM0" "ACM1"
for d in `cd /dev;ls ttyACM* | xargs`
do
	mkdir $d
done

export GRAB='~/grabserial-github/grabserial -Q -B 115200'
export TIME='1200'

echo "Test Starting..."

# Start recording from each unit
#for d in "ttyACM0"
for d in `cd /dev;ls ttyACM* | xargs`
do
	echo Capturing from $d
	bash -c "cd $d; python3 $GRAB -d /dev/$d -e $TIME -t -o %" &
done

# Wait for units finish
sleep $TIME
sleep 30

echo "Test Complete"

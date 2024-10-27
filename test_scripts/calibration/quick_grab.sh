#!/bin/bash
#

# Create target directories
for d in `cd /dev;ls ttyACM* | xargs`
do
	mkdir $d 2>/dev/null
done

export GRAB='~/grabserial-github/grabserial -Q -B 115200'

export TIME=10
if [[ $1 != "" ]]; then
	export TIME=$1
fi

echo "Test Starting, duration" $TIME
date -R

# Start recording from each unit
for d in `cd /dev;ls ttyACM* | xargs`
do
	echo Capturing from $d
	bash -c "cd $d; python3 $GRAB -d /dev/$d -e $TIME -t -o %" &
done

# Wait for units finish
sleep $TIME
sleep 10

echo "Test Complete"

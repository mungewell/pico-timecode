#!/bin/bash
#
#

export GRAB='~/grabserial-github/grabserial -Q -B 115200'
export TIME='700'

export units=`echo $@ | cut -d ' ' -f 2-`

if [[ $units == "" ]]; then
	# Assume all ttyACM's
	export units=`cd /dev;ls ttyACM* | xargs`
fi

echo "Processing:" $units

for d in $units
do
	mkdir $d 2>/dev/null
done

echo "Test Starting..."

# Start recording from each unit
for d in $units
do
	echo Capturing from $d
	bash -c "cd $d; python3 $GRAB -d /dev/$d -e $TIME -t -o %" &
done

# Wait for units finish
sleep $TIME
sleep 10

echo "Test Complete"

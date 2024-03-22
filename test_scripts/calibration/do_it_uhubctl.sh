# Modified testing script, which uses 'uhubctl' to switch on/off the power to specific hub ports
# requires spec hardware which supports this function
#
# Also need to configure the 'lib/config.py' to:
#     'monitor'   : ['Yes', ['No', 'Yes']],
#     'calibrate' : ['Always', ['No', 'Once', 'Always']],
#
# And 'main.py' to:
# if __name__ == "__main__":
#    OLED_display_thread(64)
#

export GRAB='/home/simon/grabserial-github/grabserial -Q -B 115200'
#export TIME=1200
export TIME=700

# check user
if [ `whoami` != "root" ]; then
    echo "UHUBCTL may not function as mere-user..."
fi

# Create target directories and enumerate units
export units=`cd /dev;ls ttyACM* | xargs`

for d in $units
do
	mkdir $d 2>/dev/null

	# we need to associate a USB path with each ACM
	export found=`find -L /sys/bus/usb/devices/ -maxdepth 6 -name "dev" -exec grep -Hi 166 {} \; 2>/dev/null | sort | grep -m 1 $d`
	export ${d}=`echo -n $found | cut -d ':' -f 1 | rev | cut -d '/' -f 1 | rev`
	echo "Unit: $d ${!d}"
done

echo "Test Starting..."
for i in {1..10}
do
	for d in $units
	do
		# Power each unit off
		bash -c "sudo uhubctl -l `echo ${!d} | cut -d '.' -f 1` -p `echo ${!d} | cut -d '.' -f 2 | cut -d ":" -f 1` -a off"
	done

	for d in $units
	do
		# turn each unit on, with some time to get 'started'
		bash -c "sudo uhubctl -l `echo ${!d} | cut -d '.' -f 1` -p `echo ${!d} | cut -d '.' -f 2 | cut -d ":" -f 1` -a on"
		sleep 4

		# Start recording from each unit
		echo Capturing from $d
		bash -c "cd $d; python3 $GRAB -d /dev/$d -e $TIME -t -o %" &
	done

	# Wait for units finish (automatically time out)
	sleep $TIME
	sleep 10
done
echo "Test Complete"

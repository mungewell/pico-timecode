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

export TIME=700
export CYCLES=20

export HUBLOG=/dev/null
export REPEATS=5	# instruct hub to 'turn off' port multiple times, to be sure

export GRAB='/home/simon/grabserial-github/grabserial -Q -B 115200'

# check user
if [ `whoami` != "root" ]; then
    echo "UHUBCTL may not function as mere-user..."
fi

# Create target directories and enumerate units
export units=`cd /dev;ls ttyACM* | xargs`
echo "Units: " $units

if [ -f devices.txt ]; then
	echo "'devices.txt' exists, using as cached reference."
else
	find -L /sys/bus/usb/devices/ -maxdepth 7 -name "dev" -exec grep -Hi 166 {} \; 2>/dev/null | grep "port" | sort > devices.txt
fi

for d in $units
do
	mkdir $d 2>/dev/null

	# we need to associate a USB path with each ACM
	export found=`grep -m 1 $d devices.txt`
	export ${d}=`echo -n $found | awk -F 'tty' '{print $1}'| rev | cut -d ':' -f 2  | cut -d '/' -f 1`
	echo "$found -> `echo ${!d} | rev`"
done

echo "Test Starting..."
for (( i=1; i<=$CYCLES; i++ ))
do
	echo
	echo "Cycle $i"
	date -R

	echo "Cycle $i" >> $HUBLOG
	date -R >> $HUBLOG

	for d in $units
	do
		export hub=`echo ${!d} | cut -d '.' -f 2- | rev`
		export port=`echo ${!d} | cut -d '.' -f 1 | rev`
		echo "Unit $d : Hub $hub, Port $port"

		# Power particular unit off
		bash -c "sudo uhubctl -l $hub -p $port -a off -r $REPEATS" >> $HUBLOG
		bash -c "sudo uhubctl | grep -A 10 '$hub' | grep -m 1 'Port $port'"
		sleep 2

		# Then turn unit back on
		bash -c "sudo uhubctl -l $hub -p $port -a on -r $REPEATS" >> $HUBLOG
		bash -c "sudo uhubctl | grep -A 10 '$hub' | grep -m 1 'Port $port'"
		sleep 2

		# Start recording from each unit (allowing time to start up)
		echo "  Capturing from $d"
		bash -c "sleep 20; cd $d; python3 $GRAB -d /dev/$d -e $TIME -t -o %" &
	done

	# Wait for units finish (automatically time out)
	sleep $TIME
	sleep 30
done
echo "Test Complete"

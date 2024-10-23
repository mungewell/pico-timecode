#!/bin/bash
#
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
export REPEATS=5		# instruct hub to 'turn off' port multiple times, to be sure

export GRAB='/home/simon/grabserial-github/grabserial -Q -B 115200'
export GRABLOG=/dev/null

# check user
if [ `whoami` != "root" ]; then
    echo "UHUBCTL may not function as mere-user..."
fi

if [ -f devices.txt ]; then
	echo "'devices.txt' exists, using as cached reference."
else
	# Create target directories and enumerate units
	export devices=`uhubctl | grep MicroPython | rev | cut -d ' ' -f 1 | cut -c 2- | rev`
	echo "Found: " $devices

	for d in $devices
	do
		export port=`uhubctl | grep $d | cut -d ":" -f 1 | rev | cut -d ' ' -f 1 | rev`
		export hub=`uhubctl | tac | grep -A 10 $d | grep -m 1 "Current status for hub" | cut -d ' ' -f 5`
		export tty=`ls /sys/bus/usb/devices/$hub.$port/$hub.$port\:1.0/tty/`

		echo "$d: $hub $port $tty" >> devices.txt
	done
fi

export units=`cut -d ' ' -f 4 devices.txt`
echo "Units: " $units

for d in $units
do
	mkdir $d 2>/dev/null
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
		export hub=`grep $d devices.txt | cut -d ' ' -f 2`
		export port=`grep $d devices.txt | cut -d ' ' -f 3`
		echo "Unit $d : Hub $hub, Port $port"

		# Power particular unit off
		bash -c "sudo uhubctl -l $hub -p $port -a off -r $REPEATS" >> $HUBLOG
		bash -c "sudo uhubctl | grep -A 10 '$hub ' | grep -m 1 'Port $port'"
	done

	for d in $units
	do
		export hub=`grep $d devices.txt | cut -d ' ' -f 2`
		export port=`grep $d devices.txt | cut -d ' ' -f 3`

		echo "Unit $d : Hub $hub, Port $port"

		# Then turn unit back on
		bash -c "sudo uhubctl -l $hub -p $port -a on -r $REPEATS" >> $HUBLOG
		sleep 5
		export check=`bash -c "sudo uhubctl | grep -A 10 '$hub ' | grep -m 1 'Port $port'"`
		echo "$check"

		# Did it actually turn on? Try again....
		if [[ $check != *"enable"* ]]; then
			echo "  Check failed"
			echo "Check failed" >> $HUBLOG
			bash -c "sudo uhubctl -l $hub -p $port -a on -r $REPEATS" >> $HUBLOG
			sleep 5

			export check=`bash -c "sudo uhubctl | grep -A 10 '$hub ' | grep -m 1 'Port $port'"`
			echo "$check"
		fi

		# It appears that we can't trust that ttyACMx will be consistant allocated
		export tty=`ls /sys/bus/usb/devices/$hub.$port/$hub.$port\:1.0/tty/`

		# Start recording from each unit (allowing time to start up)
		echo "  Capturing from $tty"
		bash -c "sleep 20; cd $d; python3 $GRAB -d /dev/$tty -e $TIME -t -o %" 2>&1 > $GRABLOG &
	done

	# Wait for units finish (automatically time out)
	sleep $TIME
	sleep 30
done
echo "Test Complete"

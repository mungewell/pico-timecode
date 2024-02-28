
# Create target directories
for d in "ACM0" "ACM1"
do
	mkdir $d
done

export GRAB='~/grabserial-github/grabserial -Q -B 115200'
export TIME='1200'

echo "Test Starting..."

# Start recording each unit
for d in "ACM0" "ACM1"
do
	bash -c "cd $d; python3 $GRAB -d /dev/tty$d -e $TIME -t -o %" &
done

# Wait for units finish
sleep $TIME
sleep 30

echo "Test Complete"

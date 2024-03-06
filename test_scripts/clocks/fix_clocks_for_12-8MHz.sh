# Get around the problem that you modified your XTAL, but forgot/neglected 
# to upload a modified mircoPython uf2 before you did.... :-(

# need to run as root
echo "Starting proceedure...."

openocd -f interface/cmsis-dap.cfg -f target/rp2040.cfg -c "init;halt;" &
sleep 1

# Force CPU to 120MHz (12.8MHz XTAL)
#(gdb) set *((unsigned int)0x40028000) = 0x0000002
#(gdb) set *((unsigned int)0x40028004) = 0x0000004
#(gdb) set *((unsigned int)0x4002c008) = 0x00000069
#(gdb) set *((unsigned int)0x4002c00C) = 0x00074000

# Force USB to 48MHz (12.8MHz XTAL)
#(gdb) set *((unsigned int)0x40028008) = 0x00000048
#(gdb) set *((unsigned int)0x4002800C) = 0x00042000

gdb-multiarch -ex "target extended-remote localhost:3333" \
	-ex "set *((unsigned int)0x4002c000) = 0x00000001" \
	-ex "set *((unsigned int)0x4002c004) = 0x00000004" \
	-ex "set *((unsigned int)0x4002c008) = 0x00000069" \
	-ex "set *((unsigned int)0x4002c00C) = 0x00074000" \
	-ex "set *((unsigned int)0x40028000) = 0x00000001" \
	-ex "set *((unsigned int)0x40028004) = 0x00000004" \
	-ex "set *((unsigned int)0x40028008) = 0x00000048" \
	-ex "set *((unsigned int)0x4002800C) = 0x00042000" \
	-ex "cont" &

sleep 1
killall gdb-multiarch
killall openocd

echo "Done"

# try to re-enable Hub ports
for i in `uhubctl | grep "Current" | cut -d " " -f 5| xargs`
do
	for j in `uhubctl -l $i | grep -F "connect []" | cut -c 8 | xargs`
	do
		echo $i $j
		bash -c "uhubctl -p $j -a off -l $i"
		bash -c "uhubctl -p $j -a on -l $i"
		echo
	done
done

# you can then use mpremote....
#
# $ python3 mpremote.py a1
# 
# import os
# os.remove("main.py")

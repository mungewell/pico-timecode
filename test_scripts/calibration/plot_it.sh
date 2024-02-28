

for d in "ACM0" "ACM1"
do
	# plot the Calibration
	echo > gnu.plt
	echo -n "a=[" > yrange.py

	echo "set term png small size 600,600" >> gnu.plt
	echo -n "set output \"$d" >> gnu.plt
	echo "_cal.png\"" >> gnu.plt

	echo "set multiplot" >> gnu.plt
     	echo "set size 1,0.5" >> gnu.plt
     	echo "set origin 0,0.5" >> gnu.plt

     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f -name "*"|sort|xargs`
	do
		echo "adding $i"
		echo "  \"$i\" using 0:6 with lines title \"$i\", \\" >> gnu.plt

		# store for next plot
		echo -n `tail -n 1 $i  | cut -d ' ' -f 6` >> yrange.py
		echo -n "," >> yrange.py
	done
     	echo "" >> gnu.plt
	echo "]" >> yrange.py
	echo "print('set yrange['+str(min(a)-0.2)+':'+str(max(a)+0.2)+']')" >> yrange.py

	echo "set xrange[400:1000]" >> gnu.plt
	python3 yrange.py >> gnu.plt

     	echo "set origin 0,0.0" >> gnu.plt
     	echo "set key off" >> gnu.plt

     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f -name "*"|sort|xargs`
	do
		zoom=`tail -n 1 $i  | cut -d ' ' -f 6`

		echo "  \"$i\" using 0:6 with lines title \"$i\", \\" >> gnu.plt
	done

     	echo "" >> gnu.plt
	echo "unset multiplot" >> gnu.plt
	gnuplot < gnu.plt

	# Plot the phase
	echo > gnu.plt

	echo "set term png small size 1200,600" >> gnu.plt
	echo -n "set output \"$d" >> gnu.plt
	echo "_phase.png\"" >> gnu.plt

     	echo "set yrange[-0.5:0.5]" >> gnu.plt
     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f -name "*"|sort|xargs`
	do
		echo "adding $i"
		echo "  \"$i\" using 0:4 with lines title \"$i\", \\" >> gnu.plt

	done
     	echo "" >> gnu.plt
	gnuplot < gnu.plt

done



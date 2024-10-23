#!/bin/bash

for d in `ls -d ttyACM* | xargs`
do
	# plot the Calibration
	echo > gnu.plt
	echo -n "a=[" > yrange_${d}.py

	echo "set term png large size 1024,1024" >> gnu.plt
	echo "set output \"cal_${d}.png\"" >> gnu.plt

	echo "set multiplot" >> gnu.plt
     	echo "set size 1,0.5" >> gnu.plt
     	echo "set origin 0,0.5" >> gnu.plt

     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f ! -size 0 -name "202*"|sort|xargs`
	do
		echo "adding $i"
		echo "  \"$i\" using 0:6 with lines title \"$i\", \\" >> gnu.plt

		# store last value of each log to compute averages
		echo -n `tail -n 2 $i | head -n 1 | cut -d ' ' -f 6` >> yrange_${d}.py
		echo -n "," >> yrange_${d}.py

		# capture the device name
		export name=`head -n 2 $i | tail -n 1 | cut -d ' ' -f 9`
	done
     	echo "" >> gnu.plt

	echo "]" >> yrange_${d}.py
	echo "print('set yrange['+str(min(a)-0.2)+':'+str(max(a)+0.2)+']')" >> yrange_${d}.py
	echo "print('set title \\\"$name Minimum :', min(a), 		'\\\"')" >> yrange_${d}.py
	echo "print('set title \\\"$name Maximum :', max(a), 		'\\\"')" >> yrange_${d}.py
	echo "print('set title \\\"$name Median :', (min(a) + max(a))/2,'\\\"')" >> yrange_${d}.py
	echo "print('set title \\\"$name Average :', sum(a)/len(a), 	'\\\"')" >> yrange_${d}.py

	echo "set xrange[400:600]" >> gnu.plt
	python3 yrange_${d}.py >> gnu.plt

     	echo "set origin 0,0.0" >> gnu.plt
     	echo "set key off" >> gnu.plt

     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f ! -size 0 -name "202*"|sort|xargs`
	do
		zoom=`tail -n 2 $i | head -n 1 | cut -d ' ' -f 6`

		echo "  \"$i\" using 0:6 with lines title \"$i\", \\" >> gnu.plt
	done

     	echo "" >> gnu.plt
	echo "unset multiplot" >> gnu.plt
	gnuplot < gnu.plt

	# plot Temp vs Calibration
	echo > gnu.plt

	echo "set term png small size 600,600" >> gnu.plt
	echo "set output \"temp_vs_cal_${d}.png\"" >> gnu.plt

	echo "set multiplot" >> gnu.plt
     	echo "set size 1,0.5" >> gnu.plt
     	echo "set origin 0,0.5" >> gnu.plt

     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f ! -size 0 -name "202*"|sort|xargs`
	do
		echo "adding $i"
		echo "  \"$i\" using 0:7 with lines title \"$i\", \\" >> gnu.plt
	done
     	echo "" >> gnu.plt

	echo "set xrange[400:600]" >> gnu.plt
	python3 yrange_${d}.py >> gnu.plt

     	echo "set origin 0,0.0" >> gnu.plt
     	echo "set key off" >> gnu.plt

     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f ! -size 0 -name "202*"|sort|xargs`
	do
		zoom=`tail -n 2 $i |head -n 1 | cut -d ' ' -f 6`

		echo "  \"$i\" using 0:6 with lines title \"$i\", \\" >> gnu.plt
	done

     	echo "" >> gnu.plt
	echo "unset multiplot" >> gnu.plt
	gnuplot < gnu.plt

	# Plot the phase
	echo > gnu.plt

	echo "set term png small size 1200,600" >> gnu.plt
	echo "set output \"phase_${d}.png\"" >> gnu.plt

     	echo "set yrange[-0.005:0.005]" >> gnu.plt
     	echo "plot \\" >> gnu.plt
	for i in `find $d -type f ! -size 0 -name "202*"|sort|xargs`
	do
		echo "adding $i"
		echo "  \"$i\" using 0:4 with lines title \"$i\", \\" >> gnu.plt

	done
     	echo "" >> gnu.plt
	gnuplot < gnu.plt

done



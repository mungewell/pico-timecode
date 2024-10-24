#!/bin/bash
#

if [[ $1 == "" ]]; then
	echo "You mush specify source file"
	exit
fi
export source=$1

export scale=0.05
if [[ $2 != "" ]]; then
	export scale=$2
fi

export width=`wc -l $source | cut -d " " -f 1`
export width=$(expr $width / 20)
if [[ $3 != "" ]]; then
	export width=$3
fi
if [[ $width < 1280 ]]; then
	export witdh=1280
fi
echo "Width $width"

echo "set term png small size $width,720" > gnu.plt
echo "set output \"$source.png\"" >> gnu.plt

echo "set xdata time" >> gnu.plt
echo "set timefmt \"%H:%M:%S\"" >> gnu.plt
echo "set format x \"%H:%M\"" >> gnu.plt

echo "set multiplot" >> gnu.plt

echo "set size 1,0.25" >> gnu.plt
echo "set origin 0,0.75" >> gnu.plt
echo "unset yrange" >> gnu.plt

echo "plot \"$source\" using 3:7 with lines lc rgb \"grey\" title \"\"" >> gnu.plt

echo "set size 1,0.75" >> gnu.plt
echo "set origin 0,0.0" >> gnu.plt
echo "set yrange [-$scale:$scale]" >> gnu.plt
echo "set title \"$source\"">> gnu.plt

echo "plot \"$source\" using 3:4 with lines lc rgb \"grey\" title \"\"" >> gnu.plt
echo "replot \"$source\" using 3:5 with lines lc rgb \"red\" title \"\"" >> gnu.plt

echo "unset multiplot" >> gnu.plt
gnuplot < gnu.plt

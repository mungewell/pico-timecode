
from libs import config

# optimal divider computed for CPU clock at 120MHz
xtal = 12_000_000
optimal = [
        [30.00, (1562 + (128/256))],        # new_div = 0x061a8000
        [29.97, (1564 + (16/256))],         # new_div = 0x061c1000
        [25.00, 1875],                      # new_div = 0x07530000
        [24.98, (1876 + (224/256))],        # new_div = 0x0754e000
        [24.00, (1953 + (32/256))],         # new_div = 0x07a12000
        [23.98, (1955 + (20/256))],         # new_div = 0x07a31400
    ]

def find_ideal(fps):
    ideal = 0
    for i in range(len(optimal)):
        if optimal[i][0] == fps:
            ideal = optimal[i][1]
            print("Ideal divider %f (at %f fps)" % (ideal,fps))
            break

    return ideal

def find_freq(cal, ideal):
    frac = abs(cal-int(cal))
    #print("Fractional", frac)

    if cal >= 0:
        cdiv = ideal - (abs(int(cal)/256) * (1-frac)) - (abs(int(cal+1)/256) * frac)
        cfreq = xtal * cdiv / ideal
    else:
        # (1562 + (128/256)) + ( ((8/256) * (.390625)) + ((9/256) * (1-.390625)) )
        cdiv = ideal + (abs(int(cal)/256) * (1-frac)) + (abs(int(cal-1)/256) * frac)
        cfreq = xtal * cdiv / ideal

    print("Calculated divider", cdiv)
    print("Calculated XTAL freq", cfreq)

    return cfreq

#------------------------

# loop through all available calibrations, printing out details
freqs = []
for i in range(len(optimal)):
    print()
    print("Checking", optimal[i][0], "fps")
    try:
        if int(optimal[i][0]) == optimal[i][0]:
            setting = config.calibration[str(int(optimal[i][0]))]
        else:
            setting = config.calibration[str(optimal[i][0])]

        ideal = find_ideal(optimal[i][0])
        if ideal:
            freqs.append(find_freq(float(setting), ideal))
    except:
        pass

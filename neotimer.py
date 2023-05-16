##########################################################################
# Raspberry Pi Pico - Non Blocking Timer (Neotimer)
#
# Library footprint: approx 3kB
# Instance footprint: 64-112 bytes
#
# This program shows how to implement a non-blocking delay function
# to use in your program. It is based on the neotimer library I developed
# for Arduino and Propeller 2 in Spin 2.
#
#   When you use a time.sleep() function in a program,
#   the processor stops everything it is doing until this delay is completed.
#   That is called a blocking delay, because it blocks the processor until it finishes.
# 
#   There are many times when we don't want this to happen.
#   This timer provides a way to use time delays without
#   blocking the processor, so it can do other things while the timer ends up.
#   This is called a non-blocking delay timer.
# 
#   The timer provides basic functionality to implement different ways of timing in a program.
#   You can use the timer in the following ways:
# 
#         A) Start-Stop-Restart Timer - You can start, stop and restart the timer until done.
#            ------------------------------------------------------------------------------------
#
#            start()   will reset the time (counting time) and set started and waiting true.
#            stop()    will set started and waiting false.
#                      It will also return the elapsed milliseconds since it was started
#            restart() will set the timer to started and waiting but will not reset the time.
#
# 
#             note_timer = Neotimer(200) <-------- Initializes a 200ms timer
#
#             if collision_detected:
#                 note_timer.start()    <--------- Starts timer
#                 explorer.set_tone(beep_tone)
#             if note_timer.finished():
#                 explorer.set_tone(-1) <--------- Called after 200ms
# 
# 
#         B) Periodic trigger - The following example will toggle pin 56 every 500ms
#            ------------------------------------------------------------------------------------
# 
#             led_pin = Pin(25,Pin.OUT)
#             myTimer = Neotimer(500)<---------------- Initializes a 500ms timer
#
#             while True:
#                 if(myTimer.repeat_execution())
#                   led_pin.toggle() <---------------- Called every 500ms
#
# 
#         C) Periodic trigger with count - The following example will toggle pin 56 every 500ms,
#            only 3 times. To reset the repetitions use reset_repetitions().
#            ------------------------------------------------------------------------------------
# 
#             led_pin = Pin(25,Pin.OUT)
#             button = Pin(2, Pin.IN)
#
#             myTimer = Neotimer(500)<---------------- Initializes a 500ms timer
#
#             while True:
#                 if(myTimer.repeat_execution(3)) <--- Only repeat 3 times
#                   led_pin.toggle() <---------------- Called every 500ms
#
#                 if(button.value())
#                   myTimer.reset_repetitions() <----- Reset repetitions
#
# 
#         D) Debouncer for signals - You can debounce a signal using debouce_signal.
#            The debouncing period will be duration.
#            ------------------------------------------------------------------------------------
#            In this example, the button pin value signal will
#            be debounced for 250 milliseconds:
#
#             button = Pin(2, Pin.IN)
#             presses = 0
#             myTimer = Neotimer(250) <--------------- Initializes a 250ms timer
# 
#             while True:
#                 if myTimer.debounce_signal(button.value()): <----- button pressed signal debounced for 250ms
#                     presses += 1
#                     print(presses)
# 
#
#         E) Waiting - The following example will turn on the led for 1000ms each time the button is pressed
#            ------------------------------------------------------------------------------------
# 
#             from machine import Pin
#             from neotimer import *
#
#             button = Pin(2, Pin.IN)
#             led = Pin(25,Pin.OUT)
#             led.off()
# 
#             myTimer = Neotimer(1000)
#             debouncer = Neotimer(200)
# 
#             while True:
# 
#                 if debouncer.debounce_signal(button.value()):
#                     myTimer.start()
#
#                 if myTimer.waiting():
#                     led.on()
#                 else:
#                     led.off()
# 
#
#         F) Hold Signal - If button is pressed for 1 second turn on the LED
#            ------------------------------------------------------------------------------------
#
#             from neotimer import *
#             from machine import Pin
# 
#             BUTTON_A = Pin(20,Pin.IN)
# 
#             led = Pin(25,Pin.OUT)
# 
#             myTimer = Neotimer(1000)
# 
#             while True:
#                 if myTimer.hold_signal(BUTTON_A.value()):
#                     led.on()
#                 else:
#                     led.off()
#
#
# Author: Jose Rullan
# Date: January 24, 2022
##########################################################################
#import time
from time import ticks_ms, ticks_diff

# Neotimer Class
class Neotimer:
    def __init__(self,duration):
        self.duration = duration
        self.last = ticks_ms()
        self.started = False
        self.done = False
        self.repetitions = -1 #Unlimited
    
    # Starts the timer
    def start(self):
        self.reset()
        self.started = True
    
    # Stops the timer
    def stop(self):
        self.started = False
        return self.get_elapsed()
        
    # Resets the timer
    def reset(self):
        self.stop()
        self.last = ticks_ms()
        self.done = False
        
    # Restarts the timer
    def restart(self):
        if not self.done:
            self.started = True
            
    # Returns True if the timer has finished
    def finished(self):
        if not self.started:
            return False
        
        if self.get_elapsed() >= self.duration:
            self.done = True
            return True
        else:
            return False
    
    # Returns elapsed time
    def get_elapsed(self):
        return ticks_diff(ticks_ms(),self.last)
        
    # Debounces a signal with duration
    def debounce_signal(self,signal):
        if not self.started:
            self.start()
        if signal and self.finished():
            self.start()
            return True
        else:
            return False
    
    # Returns true if a signal is on for duration
    def hold_signal(self,signal):
        if signal:
            if not self.started:
                self.start()
            return True if self.finished() else False
        
        self.reset()  #<--- Stops and resets the timer.
        return False
    
    # Returns true when timer is done and resets it
    def repeat_execution(self):
        if self.finished():
            self.reset()
            return True
        
        if not self.started:
            self.started = True
            self.last = ticks_ms()
        
        return False

    # Executes repeat_execution count times
    def repeat_execution_times(self,count):
        if count != -1:
            if self.repetitions == -1:   #<---- Initial state is -1
                self.repetitions = count
            if self.repetitions == 0:    #<---- When finished return False
                return False
            if self.repeat_execution():  #<---- Otherwise call repeat_execution()
                self.repetitions -= 1
                return True
            else:
                return False
        else:
            return self.repeat_execution() #<---- if repetitions is -1, just call repeat_execution()

    # Resets repetitions
    def reset_repetitions(self):
        self.repetitions = -1
        
    # Returns True for the duration of the timer
    def waiting(self):
        if self.started and not self.finished():
            return True
        else:
            return False

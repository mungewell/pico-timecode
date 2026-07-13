from machine import Pin,SPI,PWM
import framebuf
import time

LCD_WIDTH = 240
LCD_HEIGHT = 320

# Pin definition  
DC = 16
CS = 17
SCK = 18
MOSI = 19
MISO = None
RST = 20
BL = 15

class LCD_2in(object):
    def __init__(self):
        self.width = LCD_WIDTH
        self.height = LCD_HEIGHT
        
        self.cs = Pin(CS,Pin.OUT)
        self.rst = Pin(RST,Pin.OUT)
        
        self.cs(1)     
        self.spi = SPI(0,240_000_000,polarity=0, phase=0,sck=Pin(SCK),mosi=Pin(MOSI),miso=None)
        self.dc = Pin(DC,Pin.OUT)
        self.dc(1)
        self.init_display()
        
        self.pwm = PWM(Pin(BL))
        self.pwm.freq(5000) # Turn on the backlight
        self.set_bl_pwm(65535 * 60 // 100)
    
    def write_cmd(self, cmd):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([cmd]))
        self.cs(1)
        
    def write_data(self, buf): 
        self.dc(1)
        self.cs(0)
        self.spi.write(bytearray([buf]))
        self.cs(1)
        
    # Set screen brightness
    def set_bl_pwm(self,duty):    
        self.pwm.duty_u16(duty) # max 65535
        
    def init_display(self):
        self.rst(0)
        time.sleep_ms(100)
        self.rst(1)
        time.sleep_ms(10)
        
        self.write_cmd(0x11)      

        time.sleep_ms(120)                 

        self.write_cmd(0x36)     
        self.write_data(0x48)    

        self.write_cmd(0x3A)      
        self.write_data(0x05)    

        self.write_cmd(0xF0)      
        self.write_data(0xC3)    

        self.write_cmd(0xF0)      
        self.write_data(0x96)    

        self.write_cmd(0xB4)      
        self.write_data(0x01)    

        self.write_cmd(0xB7)      
        self.write_data(0xC6)    

        self.write_cmd(0xC0)      
        self.write_data(0x80)    
        self.write_data(0x45)    

        self.write_cmd(0xC1)      
        self.write_data(0x13)   

        self.write_cmd(0xC2)      
        self.write_data(0xA7)    

        self.write_cmd(0xC5)      
        self.write_data(0x0A)    

        self.write_cmd(0xE8)      
        self.write_data(0x40) 
        self.write_data(0x8A) 
        self.write_data(0x00) 
        self.write_data(0x00) 
        self.write_data(0x29) 
        self.write_data(0x19) 
        self.write_data(0xA5) 
        self.write_data(0x33) 

        self.write_cmd(0xE0) 
        self.write_data(0xD0) 
        self.write_data(0x08) 
        self.write_data(0x0F) 
        self.write_data(0x06) 
        self.write_data(0x06) 
        self.write_data(0x33) 
        self.write_data(0x30) 
        self.write_data(0x33) 
        self.write_data(0x47) 
        self.write_data(0x17) 
        self.write_data(0x13) 
        self.write_data(0x13) 
        self.write_data(0x2B) 
        self.write_data(0x31) 

        self.write_cmd(0xE1) 
        self.write_data(0xD0) 
        self.write_data(0x0A) 
        self.write_data(0x11) 
        self.write_data(0x0B) 
        self.write_data(0x09) 
        self.write_data(0x07) 
        self.write_data(0x2F) 
        self.write_data(0x33) 
        self.write_data(0x47) 
        self.write_data(0x38) 
        self.write_data(0x15) 
        self.write_data(0x16) 
        self.write_data(0x2C) 
        self.write_data(0x32) 
        

        self.write_cmd(0xF0)      
        self.write_data(0x3C)    

        self.write_cmd(0xF0)      
        self.write_data(0x69)    

        time.sleep_ms(120)                 

        self.write_cmd(0x21)      

        self.write_cmd(0x29)   
         
    #def setWindows(self, Xstart, Ystart, Xend, Yend):
    def set_windows(self, Xstart, Ystart, Xend, Yend):
        self.write_cmd(0x2A)
        self.write_data(Xstart >> 8)
        self.write_data(Xstart)
        self.write_data((Xend-1) >> 8)
        self.write_data(Xend-1)
        
        self.write_cmd(0x2B)
        self.write_data((Ystart) >> 8)
        self.write_data(Ystart)
        self.write_data(((Yend)-1) >> 8)
        self.write_data((Yend)-1)
        self.write_cmd(0x2C)
        
    def draw_point(self, x, y, color):
        self.set_windows(x, y, x, y)
        self.dc(1)
        self.cs(0)
        self.spi.write(bytearray([color >> 8, color & 0x00ff]))
        self.cs(1)
        
    def draw_square(self, x, y, s,color):
        x_start = x
        y_start = y
        x_end = x + s
        y_end = y + s
        
        self.set_windows(x_start, y_start, x_end, y_end)
        self.dc(1)
        self.cs(0)
        for i in range((s+1)*(s+1)):
            self.spi.write(bytearray([color >> 8, color & 0x00ff]))
        self.cs(1)
        
    def lcd_fill(self, color):
        buffer = bytearray([color >> 8, color & 0x00ff] * LCD_WIDTH)
        self.set_windows(0, 0, LCD_WIDTH, LCD_HEIGHT)
        self.dc(1)
        self.cs(0)
        for i in range(LCD_HEIGHT):
            self.spi.write(buffer)
        self.cs(1)
        
    def show(self): 
        self.setWindows(0,0,self.width,self.height)
        
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)
        
    '''
        Partial display, the starting point of the local
        display here is reduced by 10, and the end point
        is increased by 10
    '''
    # Partial display, the starting point of the local display here is reduced by 10, and the end point is increased by 10
    def Windows_show(self,Xstart,Ystart,Xend,Yend):
        if Xstart > Xend:
            data = Xstart
            Xstart = Xend
            Xend = data
            
        if (Ystart > Yend):        
            data = Ystart
            Ystart = Yend
            Yend = data
            
        if Xstart <= 10:
            Xstart = 10
        if Ystart <= 10:
            Ystart = 10
            
        Xstart -= 10;Xend += 10
        Ystart -= 10;Yend += 10
        
        self.setWindows(Xstart,Ystart,Xend,Yend)      
        self.cs(1)
        self.dc(1)
        self.cs(0)
        for i in range (Ystart,Yend-1):             
            Addr = (Xstart * 2) + (i * 240 * 2)                
            self.spi.write(self.buffer[Addr : Addr+((Xend-Xstart)*2)])
        self.cs(1)
        
    # def set_bl_light(self, light): #Set screen brightness  
    #     self.light.duty_u16(light)#max 65535

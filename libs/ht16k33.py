class HT16K33:
    """
    A simple, generic driver for the I2C-connected Holtek HT16K33 controller chip.
    This release supports MicroPython and CircuitPython

    Bus:        I2C
    Author:     Tony Smith (@smittytone)
    License:    MIT
    Copyright:  2025
    """

    # *********** CONSTANTS **********

    HT16K33_GENERIC_DISPLAY_ON = 0x81
    HT16K33_GENERIC_DISPLAY_OFF = 0x80
    HT16K33_GENERIC_SYSTEM_ON = 0x21
    HT16K33_GENERIC_SYSTEM_OFF = 0x20
    HT16K33_GENERIC_DISPLAY_ADDRESS = 0x00
    HT16K33_GENERIC_CMD_BRIGHTNESS = 0xE0
    HT16K33_GENERIC_CMD_BLINK = 0x81

    # *********** PRIVATE PROPERTIES **********

    i2c = None
    address = 0
    brightness = 15
    blink_rate = 0
    display_on = False

    # *********** CONSTRUCTOR **********

    def __init__(self, i2c, i2c_address, do_enable_display=True):
        assert 0x00 <= i2c_address < 0x80, "ERROR - Invalid I2C address in HT16K33()"
        self.i2c = i2c
        self.address = i2c_address
        self._power(True, do_enable_display)

    # *********** PUBLIC METHODS **********

    def set_blink_rate(self, rate=0):
        """
        Set the display's flash rate.

        Only four values (in Hz) are permitted: 0, 2, 1, and 0.5.

        If the display is off, the applied blink rate will not show until
        the display is turned on (with `.display_on()`).

        Args:
            rate (int): The chosen flash rate. Default: 0Hz (no flash).
        """
        allowed_rates = (0, 2, 1, 0.5)
        assert rate in allowed_rates, "ERROR - Invalid blink rate set in set_blink_rate()"
        self.blink_rate = allowed_rates.index(rate) & 0x03
        self._display(self.display_on)

    def set_brightness(self, brightness=15):
        """
        Set the display's brightness (ie. duty cycle).

        Brightness values range from 0 (dim, but not off) to 15 (max. brightness).

        Args:
            brightness (int): The chosen flash rate. Default: 15 (100%).
        """
        if brightness < 0 or brightness > 15: brightness = 15
        self.brightness = brightness
        self._write_cmd(self.HT16K33_GENERIC_CMD_BRIGHTNESS | brightness)

    def draw(self):
        """
        Writes the current display buffer to the display itself.

        Call this method after updating the buffer to update
        the LED itself.
        """
        self._render()

    def update(self):
        """
        Alternative for draw() for backwards compatibility
        """
        self._render()

    def clear(self):
        """
        Clear the buffer.

        Returns:
            The instance (self)
        """
        for i in range(0, len(self.buffer)): self.buffer[i] = 0x00
        return self

    def power_on(self, enable_display=True):
        """
        Power on the controller and optionally turn on the display.
        """
        self._power(True, enable_display)

    def power_off(self):
        """
        Turn off the display and power down the controller.
        """
        self._power(False)

    def display_on(self):
        """
        Turn on the display.
        """
        self._display(True)

    def display_off(self):
        """
        Turn on the display.
        """
        self._display(False)

    def is_display_on(self):
        """
        Is the display enabled?
        """
        return self.display_on

    # ********** PRIVATE METHODS **********

    def _render(self):
        """
        Write the display buffer out to I2C
        """
        buffer = bytearray(len(self.buffer) + 1)
        buffer[1:] = self.buffer
        buffer[0] = 0x00
        self.i2c.writeto(self.address, bytes(buffer))

    def _power(self, on=True, enable_display=True):
        """
        Power the controller on or off and enable the display.

        Pass `False` as the second argument to prevent the display from being
        auto-enabled.
        """
        if on:
            self._write_cmd(self.HT16K33_GENERIC_SYSTEM_ON)
            if enable_display:
                self._display(True)
        else:
            self._display(False)
            self._write_cmd(self.HT16K33_GENERIC_SYSTEM_OFF)

    def _display(self, on=True):
        """
        Turn the display on/off, preserving the blink rate.
        """
        cmd = self.HT16K33_GENERIC_DISPLAY_ON if on else self.HT16K33_GENERIC_DISPLAY_OFF
        self._write_cmd(cmd | self.blink_rate << 1)
        self.display_on = on

    def _write_cmd(self, byte):
        """
        Writes a single command to the HT16K33. A private method.
        """
        self.i2c.writeto(self.address, bytes([byte]))

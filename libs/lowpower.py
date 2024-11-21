# Raspberry Pico micropython low-power workaround
# Copyright (C) 2021 Tom Jorquera
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

REG_IO_BANK0_BASE = 0x40014000
REG_IO_BANK0_INTR0 = 0x0F0
REG_IO_BANK0_DORMANT_WAKE_INTE0 = 0x160

IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_EDGE_HIGH_BITS = 0x00000008
IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_EDGE_LOW_BITS = 0x00000004
IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_LEVEL_HIGH_BITS = 0x00000002
IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_LEVEL_LOW_BITS = 0x00000001

REG_XOSC_BASE = 0x40024000
REG_XOSC_DORMANT = 0x08
REG_XOSC_STATUS = 0x04

XOSC_DORMANT_VALUE_DORMANT = 0x636F6D61
XOSC_STATUS_STABLE_BITS = 0x80000000

# Helper values to set individual pin modes
EDGE_HIGH = IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_EDGE_HIGH_BITS
EDGE_LOW = IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_EDGE_LOW_BITS
LEVEL_HIGH = IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_LEVEL_HIGH_BITS
LEVEL_LOW = IO_BANK0_DORMANT_WAKE_INTE0_GPIO0_LEVEL_LOW_BITS

ROSC_BASE = 0x40060000

# Clock
CLOCKS_BASE = 0x40008000
CLK_REF_CTRL = 0x30
CLK_SYS_CTRL = 0x3C
CLK_PERI_CTRL = 0x48
CLK_USB_CTRL = 0x54
CLK_ADC_CTRL = 0x60
CLK_RTC_CTRL = 0x6C
PLL_SYS_BASE = 0x40028000
PLL_USB_BASE = 0x4002C000
PLL_PWR = 0x4


@micropython.asm_thumb
def _read_bits(r0):
    ldr(r0, [r0, 0])


@micropython.asm_thumb
def _write_bits(r0, r1):
    str(r1, [r0, 0])


def dormant_with_modes(pin_modes):
    registers_events = {}
    for gpio_pin, pin_mode in pin_modes.items():
        if not isinstance(gpio_pin, int) or gpio_pin < 0 or gpio_pin > 28:
            raise RuntimeError(
                "Invalid value for pin "
                + str(gpio_pin)
                + " (expect int between 0 and 27)"
            )

        if not isinstance(pin_mode, int) or pin_mode < 1 or pin_mode > 15:
            raise RuntimeError(
                "Invalid value for pin_mode "
                + str(pin_mode)
                + " (expect int between 0 and 15)"
            )

        # clear irq
        _write_bits(
            REG_IO_BANK0_BASE + REG_IO_BANK0_INTR0 + int(gpio_pin / 8) * 4,
            pin_mode << 4 * (gpio_pin % 8),
        )
        en_reg = (
            REG_IO_BANK0_BASE + REG_IO_BANK0_DORMANT_WAKE_INTE0 + int(gpio_pin / 8) * 4
        )

        if en_reg not in registers_events:
            registers_events[en_reg] = 0
        registers_events[en_reg] = registers_events[en_reg] | (
            pin_mode << 4 * (gpio_pin % 8)
        )

    # Enable Wake-up from GPIO IRQ
    for en_reg, events in registers_events.items():
        _write_bits(en_reg, events)

    # Setup clocks for going dormant
    _write_bits(CLOCKS_BASE + CLK_REF_CTRL, 0x02)
    _write_bits(CLOCKS_BASE + CLK_SYS_CTRL, 0x00)
    _write_bits(CLOCKS_BASE + CLK_USB_CTRL, 0x00)
    _write_bits(CLOCKS_BASE + CLK_ADC_CTRL, 0x00)
    _write_bits(CLOCKS_BASE + CLK_RTC_CTRL, 0x60)
    _write_bits(CLOCKS_BASE + CLK_PERI_CTRL, 0x00)
    _write_bits(PLL_USB_BASE + PLL_PWR, 0x2D)
    _write_bits(ROSC_BASE, 0xD1EAA0)

    # Go dormant
    _write_bits(REG_XOSC_BASE + REG_XOSC_DORMANT, XOSC_DORMANT_VALUE_DORMANT)

    ###
    # This part will happen after the pico wakes up
    ###

    # Normalize clocks
    _write_bits(CLOCKS_BASE + CLK_REF_CTRL, 0x02)
    _write_bits(CLOCKS_BASE + CLK_SYS_CTRL, 0x01)
    _write_bits(CLOCKS_BASE + CLK_USB_CTRL, 0x800)
    _write_bits(CLOCKS_BASE + CLK_ADC_CTRL, 0x800)
    _write_bits(CLOCKS_BASE + CLK_RTC_CTRL, 0x800)
    _write_bits(CLOCKS_BASE + CLK_PERI_CTRL, 0x800)
    _write_bits(PLL_USB_BASE + PLL_PWR, 0x04)
    _write_bits(ROSC_BASE, 0xFFFAA0)

    while not _read_bits(REG_XOSC_BASE + REG_XOSC_STATUS) & XOSC_STATUS_STABLE_BITS:
        pass

    for gpio_pin, pin_mode in pin_modes.items():
        # clear irq
        _write_bits(
            REG_IO_BANK0_BASE + REG_IO_BANK0_INTR0 + int(gpio_pin / 8) * 4,
            pin_mode << 4 * (gpio_pin % 8),
        )


def dormant_until_pins(gpio_pins, edge=True, high=True):
    low = not high
    level = not edge

    if level and low:
        event = LEVEL_LOW
    if level and high:
        event = LEVEL_HIGH
    if edge and low:
        event = EDGE_LOW
    if edge and high:
        event = EDGE_HIGH

    dormant_with_modes({pin: event for pin in gpio_pins})


def dormant_until_pin(gpio_pin, edge=True, high=True):
    dormant_until_pins([gpio_pin], edge, high)


@micropython.asm_thumb
def lightsleep():
    wfi()

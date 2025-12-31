#!/usr/bin/python2
"""
Simple script to read and display MOD-IO input states.
Use this for quick checks without starting the generator.

Usage: python2 read_inputs.py [--loop]
"""

import smbus
import time
import sys

BUS = smbus.SMBus(0)
MODIO_ADDR = 0x58
REG_INPUT = 0x20

def read_inputs():
    """Read and display input states"""
    status = BUS.read_byte_data(MODIO_ADDR, REG_INPUT)
    in1 = (status & 0b0001) != 0
    in2 = (status & 0b0010) != 0
    in3 = (status & 0b0100) != 0
    in4 = (status & 0b1000) != 0

    print "Status byte: %d (binary: %s)" % (status, bin(status))
    print "  IN1 (blue-black):  %s" % ("ACTIVE" if in1 else "inactive")
    print "  IN2 (blue):        %s" % ("ACTIVE" if in2 else "inactive")
    print "  IN3 (blue-white):  %s" % ("ACTIVE" if in3 else "inactive")
    print "  IN4 (green-black): %s" % ("ACTIVE" if in4 else "inactive")
    print ""

    if status == 0:
        print "Interpretation: Generator OFF/IDLE"
    elif status == 3:
        print "Interpretation: Generator RUNNING"
    else:
        print "Interpretation: Unknown state"

    return status

if __name__ == "__main__":
    if "--loop" in sys.argv:
        print "Continuous monitoring (Ctrl+C to stop)"
        print "-" * 40
        last = -1
        try:
            while True:
                status = BUS.read_byte_data(MODIO_ADDR, REG_INPUT)
                if status != last:
                    print "STATUS CHANGE: %d -> %d (bin: %s)" % (last, status, bin(status))
                    last = status
                time.sleep(0.1)
        except KeyboardInterrupt:
            print "\nStopped."
    else:
        read_inputs()

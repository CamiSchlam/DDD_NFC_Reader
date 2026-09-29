# Minimal MicroPython driver for the PN532 NFC module over I2C (polling mode, no IRQ pin used)
import time

_PREAMBLE = b"\x00\x00\xff"
_ACK_FRAME = b"\x00\x00\xff\x00\xff\x00"
_HOSTTOPN532 = 0xD4
_PN532TOHOST = 0xD5


class PN532Error(Exception):
    pass


class PN532_I2C:
    def __init__(self, i2c, addr=0x24):
        self.i2c = i2c
        self.addr = addr

    def _write_frame(self, data):
        length = len(data)
        frame = bytearray(_PREAMBLE)
        frame.append(length & 0xFF)
        frame.append((~length + 1) & 0xFF)  # length checksum
        frame += data
        checksum = sum(data) & 0xFF
        frame.append((~checksum + 1) & 0xFF)  # data checksum
        frame.append(0x00)  # postamble
        frame = bytes(frame)
        # The PN532 can NACK the first write after power-up/idle; retry a few times.
        last_err = None
        for _ in range(5):
            try:
                self.i2c.writeto(self.addr, frame)
                return
            except OSError as e:
                last_err = e
                time.sleep_ms(20)
        raise last_err

    def _read_ready_frame(self, length, timeout_ms):
        # The PN532 NACKs the whole I2C read (raising OSError EIO) until it is
        # ready, so the ready-check and the data read must be one transaction.
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
            try:
                buf = self.i2c.readfrom(self.addr, length + 1)
            except OSError:
                time.sleep_ms(10)
                continue
            if buf[0] & 0x01:
                return buf[1:]
            time.sleep_ms(10)
        raise PN532Error("Timeout waiting for PN532 response")

    def _read_ack(self):
        buf = self._read_ready_frame(6, 1000)
        if buf != _ACK_FRAME:
            raise PN532Error("Bad ACK frame: {}".format(buf))

    def _read_response(self, response_len, timeout_ms=1000):
        buf = self._read_ready_frame(response_len + 8, timeout_ms)
        if buf[0:3] != _PREAMBLE:
            raise PN532Error("Bad response preamble: {}".format(buf))
        length = buf[3]
        if (length + buf[4]) & 0xFF != 0:
            raise PN532Error("Bad length checksum")
        if buf[5] != _PN532TOHOST:
            raise PN532Error("Bad TFI byte")
        return bytes(buf[6 : 6 + length - 1])

    def call_function(self, command, params=b"", response_len=0, timeout_ms=1000):
        data = bytearray([_HOSTTOPN532, command]) + bytearray(params)
        self._write_frame(data)
        self._read_ack()
        response = self._read_response(response_len, timeout_ms)
        if response[0] != (command + 1) & 0xFF:
            raise PN532Error("Unexpected response command byte")
        return response[1:]

    def get_firmware_version(self):
        response = self.call_function(0x02, response_len=4)
        return tuple(response[0:4])  # (IC, Ver, Rev, Support)

    def SAM_configuration(self):
        # mode=normal, timeout=20*50ms, irq unused (polling)
        self.call_function(0x14, params=b"\x01\x14\x01", response_len=0)

    def read_passive_target(self, card_baud=0x00, timeout_ms=1000):
        try:
            response = self.call_function(
                0x4A, params=bytes([0x01, card_baud]), response_len=20, timeout_ms=timeout_ms
            )
        except PN532Error:
            return None
        if response[0] != 1:  # number of targets found
            return None
        uid_length = response[5]
        return bytes(response[6 : 6 + uid_length])

    def ntag2xx_write_block(self, block_number, data):
        if len(data) != 4:
            raise ValueError("data must be 4 bytes (one NTAG page)")
        params = bytes([0x01, 0xA2, block_number]) + bytes(data)
        response = self.call_function(0x40, params=params, response_len=1)
        return response[0] == 0x00

    def ntag2xx_read_block(self, block_number):
        params = bytes([0x01, 0x30, block_number])
        response = self.call_function(0x40, params=params, response_len=17)
        if response[0] != 0x00:
            return None
        return response[1:5]

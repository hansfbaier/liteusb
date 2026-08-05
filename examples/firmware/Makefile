# DECA EHCI Keyboard Host — firmware build
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

# Toolchain: adjust to your installed RISC-V GCC.
# LiteX installs riscv64-unknown-elf-; Debian/Ubuntu distro packages
# use riscv64-linux-gnu-.  Override with: make CROSS_COMPILE=...
CROSS_COMPILE ?= riscv64-unknown-elf-
CC             = $(CROSS_COMPILE)gcc
OBJCOPY        = $(CROSS_COMPILE)objcopy
SIZE           = $(CROSS_COMPILE)size

# RISC-V 32-bit (VexRiscv is RV32IM), bare-metal
# -msmall-data-limit=0: no .sdata/.sbss — the linker script and crt0
# only place .data/.bss; small statics must not end up in unplaced
# small-data sections (they would neither be copied nor zeroed at boot).
ARCH_FLAGS = -march=rv32im -mabi=ilp32

CFLAGS  = $(ARCH_FLAGS) -Os -g -Wall -Wextra -ffreestanding -nostdlib
CFLAGS += -I. -fno-builtin -fno-stack-protector -fno-pic -mno-relax -msmall-data-limit=0
LDFLAGS = $(ARCH_FLAGS) -nostdlib -T linker.ld -mno-relax -no-pie

TARGET = firmware

SRCS = crt0.S main.c
OBJS = crt0.o main.o

all: $(TARGET).bin $(TARGET).elf

$(TARGET).elf: $(OBJS) linker.ld
	$(CC) $(LDFLAGS) -o $@ $(OBJS) -lgcc
	$(SIZE) $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@
	@echo "Built $@ ($$(wc -c < $@) bytes)"

%.o: %.c ehci.h
	$(CC) $(CFLAGS) -c -o $@ $<

%.o: %.S
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET).elf $(TARGET).bin

.PHONY: all clean

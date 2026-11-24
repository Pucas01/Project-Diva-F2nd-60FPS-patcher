import json
import os
import re
import struct
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, 'build')
OUT_HEADER = os.path.join(HERE, '..', 'plugin', 'patches.h')
TOOLCHAIN = os.environ.get('VITASDK', '/usr/local/vitasdk') + '/bin/arm-vita-eabi-'
STOCK_EBOOT = os.environ['DIVA_EU_EBOOT']

SEG0_BASE = 0x81000000
SEG0_SIZE = 0x71B000
SEG1_BASE = 0x8171B000
SEG1_SIZE = 0x80000
CAVE_OFF = 0x71AB00
CAVE_VA = SEG0_BASE + CAVE_OFF
CAVE_LIMIT = 0x71AD00
DATA_VA = 0x81E42070
TWEEN_SITE = 0x8120E6C2

GAME_SYMBOLS = {
    'pad_read':         0x8165DC38,
    'wait_vblanks':     0x814E10B1,
    'vtable_diva_room': 0x8170AC44,
    'vtable_menu_miku': 0x81714504,
    'vtable_song':      0x8171441C,
    'task_list':        0x81750810,
    'frame_counter':    0x81787B1C,
    'fps':              0x81787C24,
}

FIXED_SITES = [
    ('s_vblank', 0x8100A994, ['bl\thook', 'nop']),
    ('s_getter', 0x8100C54A, ['b.w\tget_scale']),
    ('s_f3',     0x8158E3DC, ['bl\tget_ui_scale']),
    ('s_gate',   0x81014780, ['bl\ttick_gate_stub']),
]

C_FLAGS = ['-O2', '-mthumb', '-mcpu=cortex-a9', '-mfpu=vfpv3', '-mfloat-abi=hard',
           '-mword-relocations', '-ffreestanding', '-fno-pic', '-nostdlib', '-Wall']


def run(tool, *args):
    subprocess.run([TOOLCHAIN + tool, *args], check=True, cwd=BUILD)


def site_section(name, body):
    lines = '\n'.join('\t' + line for line in body)
    return f'\t.syntax unified\n\t.thumb\n\t.section .{name}, "ax"\n\t.thumb_func\n{lines}\n\n'


def build_sites(sites):
    asm = ''
    link = [f'{name} = {va:#x};' for name, va in GAME_SYMBOLS.items()]
    link.append('SECTIONS {')
    pieces = []
    for name, va, body in FIXED_SITES:
        asm += site_section(name, body)
        link.append(f'\t.{name} {va:#x} : {{ *(.{name}) }}')
        pieces.append((name, va))
    for i, (va, reg) in enumerate(sites):
        va = int(va, 16)
        target = 'tween_stub' if va == TWEEN_SITE else f'shim_r{reg}'
        name = f's_c{i}'
        asm += site_section(name, [f'bl\t{target}', 'nop', 'nop'])
        link.append(f'\t.{name} {va:#x} : {{ *(.{name}) }}')
        pieces.append((name, va))
    link.append(f'\t.cave {CAVE_VA:#x} : {{ *(.text .text.* .rodata .rodata.*) }}')
    link.append(f'\t.dat {DATA_VA:#x} : {{ *(.dat) }}')
    link.append('}')
    return asm, '\n'.join(link) + '\n', pieces


def compile_and_link(sites):
    os.makedirs(BUILD, exist_ok=True)
    asm, link, pieces = build_sites(sites)
    open(os.path.join(BUILD, 'sites.s'), 'w').write(asm)
    open(os.path.join(BUILD, 'patch.ld'), 'w').write(link)
    run('gcc', *C_FLAGS, '-c', os.path.join(HERE, 'cave.c'), '-o', 'cave.o')
    run('as', '-mcpu=cortex-a9', '-mfpu=vfpv3', '-o', 'stubs.o', os.path.join(HERE, 'stubs.s'))
    run('as', '-mcpu=cortex-a9', '-mfpu=vfpv3', '-o', 'sites.o', 'sites.s')
    run('ld', '-q', '-T', 'patch.ld', '-o', 'patch.elf', 'cave.o', 'stubs.o', 'sites.o')
    for name in [n for n, va in pieces] + ['cave', 'dat']:
        run('objcopy', '-O', 'binary', '-j', '.' + name, 'patch.elf', name + '.bin')
    return pieces


def read_bin(name):
    return open(os.path.join(BUILD, name + '.bin'), 'rb').read()


def cave_relocations(cave, data_len):
    listing = subprocess.check_output([TOOLCHAIN + 'readelf', '-rW', 'patch.elf'], text=True, cwd=BUILD)
    relocs = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] != 'R_ARM_ABS32':
            continue
        where = int(fields[0], 16)
        if not CAVE_VA <= where < CAVE_VA + len(cave):
            continue
        off = where - CAVE_VA
        target = struct.unpack_from('<I', cave, off)[0]
        if SEG0_BASE <= target < SEG0_BASE + SEG0_SIZE:
            relocs.append((off, 'RELOC_SEG0', target - SEG0_BASE))
        elif SEG1_BASE <= target < SEG1_BASE + SEG1_SIZE:
            relocs.append((off, 'RELOC_SEG1', target - SEG1_BASE))
        elif DATA_VA <= target < DATA_VA + data_len:
            relocs.append((off, 'RELOC_DATA', target - DATA_VA))
        else:
            raise SystemExit(f'literal at cave+{off:#x} points outside the game: {target:#x}')
    return relocs


def segment0_file_offset(eboot):
    header = struct.unpack_from('<4sIHHIQQQQQQQQQQQQQ', eboot, 0)
    return struct.unpack_from('<Q', eboot, header[14])[0]


def hex_bytes(data, width=0):
    padded = list(data) + [0] * (width - len(data))
    return ', '.join(f'0x{b:02X}' for b in padded)


def write_header(pieces, cave, data, relocs):
    eboot = open(STOCK_EBOOT, 'rb').read()
    seg0 = segment0_file_offset(eboot)

    out = [
        'typedef struct { uint32_t offset; uint8_t size; uint8_t stock[8]; uint8_t patched[8]; } patch_t;',
        'static const patch_t PATCHES[] = {',
    ]
    for name, va in pieces:
        offset = va - SEG0_BASE
        patched = read_bin(name)
        assert len(patched) <= 8
        stock = eboot[seg0 + offset:seg0 + offset + len(patched)]
        out.append(f'\t{{ 0x{offset:X}, {len(patched)}, {{ {hex_bytes(stock, 8)} }}, {{ {hex_bytes(patched, 8)} }} }},')
    out += [
        '};',
        f'#define CAVE_OFF 0x{CAVE_OFF:X}u',
        f'static const uint8_t CAVE[] = {{ {hex_bytes(cave)} }};',
        'enum { RELOC_SEG0 = 0, RELOC_SEG1 = 1, RELOC_DATA = 2 };',
        'typedef struct { uint16_t cave_off; uint8_t kind; uint32_t addend; } cave_reloc_t;',
        'static const cave_reloc_t CAVE_RELOCS[] = {',
    ]
    out += [f'\t{{ 0x{off:X}, {kind}, 0x{addend:X}u }},' for off, kind, addend in relocs]
    out += [
        '};',
        f'static const uint8_t DATA_INIT[] = {{ {hex_bytes(data)} }};',
    ]
    open(OUT_HEADER, 'w').write('\n'.join(out) + '\n')


def main():
    sites = json.load(open(os.path.join(HERE, 'sites.json')))
    pieces = compile_and_link(sites)
    cave = read_bin('cave')
    data = read_bin('dat')
    assert CAVE_OFF + len(cave) <= CAVE_LIMIT, f'cave ends at {CAVE_OFF + len(cave):#x}, past the page end'
    relocs = cave_relocations(cave, len(data))
    write_header(pieces, cave, data, relocs)
    print(f'{len(pieces)} patches, {len(cave)} byte cave at seg0+{CAVE_OFF:#x}, {len(relocs)} relocations')


main()

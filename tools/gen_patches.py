import json
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, 'build')
OUT_HEADER = os.path.join(HERE, '..', 'plugin', 'patches.h')
VITA3K_DIR = os.path.join(HERE, '..', 'vita3k')
TOOLCHAIN = os.environ.get('VITASDK', '/usr/local/vitasdk') + '/bin/arm-vita-eabi-'
TARGET = sys.argv[1] if len(sys.argv) > 1 else 'plugin'
if TARGET not in ('plugin', 'vita3k'):
    raise SystemExit('usage: gen_patches.py [plugin|vita3k]')

SEG0_BASE = 0x81000000
SEG1_SPAN = 0x80000
PLUGIN_DATA_VA = 0x81E42070
CAVE_SLOT = 0x200

C_FLAGS = ['-O2', '-mthumb', '-mcpu=cortex-a9', '-mfpu=vfpv3', '-mfloat-abi=hard',
           '-mword-relocations', '-ffreestanding', '-fno-pic', '-nostdlib', '-Wall']

FIXED_BODIES = {
    's_vblank': ['bl\thook', 'nop'],
    's_getter': ['b.w\tget_scale'],
    's_f3':     ['bl\tget_ui_scale'],
    's_gate':   ['bl\ttick_gate_stub'],
}
FIXED_ORDER = ['s_vblank', 's_getter', 's_f3', 's_gate']
BL_SITES = {'s_vblank': 2, 's_f3': 0}
TITLE_ENV = {'PCSB00554': 'DIVA_EU_EBOOT', 'PCSE00434': 'DIVA_US_EBOOT', 'PCSG00205': 'DIVA_JP_EBOOT'}


def load_regions():
    regions = json.load(open(os.path.join(HERE, 'regions.json')))
    out = []
    for name, r in regions.items():
        out.append({
            'name': name,
            'env': f'DIVA_{name}_EBOOT',
            'titles': r['titles'],
            'seg0_size': int(r['seg0_size'], 16),
            'seg1_base': int(r['seg1_base'], 16),
            'symbols': {k: int(v, 16) for k, v in r['symbols'].items()},
            'fixed': {k: int(v, 16) for k, v in r['fixed'].items()},
            'sites': [int(va, 16) for va, reg in r['sites']],
            'regs': [reg for va, reg in r['sites']],
            'tween_site': int(r['tween_site'], 16),
            'cave_off': {k: int(v, 16) for k, v in r['cave_off'].items()},
        })
    return out


def run(tool, *args):
    subprocess.run([TOOLCHAIN + tool, *args], check=True, cwd=BUILD)


def site_section(name, body):
    lines = '\n'.join('\t' + line for line in body)
    return f'\t.syntax unified\n\t.thumb\n\t.section .{name}, "ax"\n\t.thumb_func\n{lines}\n\n'


def build_sites(region, cave_va, data_va):
    asm = ''
    link = [f'{name} = {va:#x};' for name, va in region['symbols'].items()]
    link.append('SECTIONS {')
    pieces = []
    for name in FIXED_ORDER:
        va = region['fixed'][name]
        asm += site_section(name, FIXED_BODIES[name])
        link.append(f'\t.{name} {va:#x} : {{ *(.{name}) }}')
        pieces.append((name, va))
    for i, (va, reg) in enumerate(zip(region['sites'], region['regs'])):
        target = 'tween_stub' if va == region['tween_site'] else f'shim_r{reg}'
        name = f's_c{i}'
        asm += site_section(name, [f'bl\t{target}', 'nop', 'nop'])
        link.append(f'\t.{name} {va:#x} : {{ *(.{name}) }}')
        pieces.append((name, va))
    link.append(f'\t.cave {cave_va:#x} : {{ *(.text .text.* .rodata .rodata.*) }}')
    link.append(f'\t.dat {data_va:#x} : {{ *(.dat) }}')
    link.append('}')
    return asm, '\n'.join(link) + '\n', pieces


def compile_and_link(region, cave_va, data_va):
    os.makedirs(BUILD, exist_ok=True)
    asm, link, pieces = build_sites(region, cave_va, data_va)
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


def cave_relocations(region, cave, cave_va, data_va, data_len):
    listing = subprocess.check_output([TOOLCHAIN + 'readelf', '-rW', 'patch.elf'], text=True, cwd=BUILD)
    seg1_base = region['seg1_base']
    relocs = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] != 'R_ARM_ABS32':
            continue
        where = int(fields[0], 16)
        if not cave_va <= where < cave_va + len(cave):
            continue
        off = where - cave_va
        target = struct.unpack_from('<I', cave, off)[0]
        if SEG0_BASE <= target < SEG0_BASE + region['seg0_size']:
            relocs.append((off, 'RELOC_SEG0', target - SEG0_BASE))
        elif seg1_base <= target < seg1_base + SEG1_SPAN:
            relocs.append((off, 'RELOC_SEG1', target - seg1_base))
        elif data_va <= target < data_va + data_len:
            relocs.append((off, 'RELOC_DATA', target - data_va))
        else:
            raise SystemExit(f'literal at cave+{off:#x} points outside the game: {target:#x}')
    return relocs


def segment0_file_offset(eboot):
    header = struct.unpack_from('<4sIHHIQQQQQQQQQQQQQ', eboot, 0)
    return struct.unpack_from('<Q', eboot, header[14])[0]


def hex_bytes(data, width=0):
    padded = list(data) + [0] * (width - len(data))
    return ', '.join(f'0x{b:02X}' for b in padded)


def build_region(region, target):
    cave_off = region['cave_off'][target]
    cave_va = SEG0_BASE + cave_off
    data_va = PLUGIN_DATA_VA if target == 'plugin' else cave_va + CAVE_SLOT
    pieces = compile_and_link(region, cave_va, data_va)
    cave = read_bin('cave')
    data = read_bin('dat')
    assert len(cave) <= CAVE_SLOT, f'{region["name"]}: cave is {len(cave):#x} bytes, slot is {CAVE_SLOT:#x}'
    relocs = cave_relocations(region, cave, cave_va, data_va, len(data)) if target == 'plugin' else []
    patches = [(name, va - SEG0_BASE, read_bin(name)) for name, va in pieces]
    return {'region': region, 'cave_off': cave_off, 'cave': cave, 'data': data,
            'data_off': data_va - SEG0_BASE, 'relocs': relocs, 'patches': patches}


def check_against_reference(built, eboot, reference, label):
    seg0 = segment0_file_offset(eboot)
    ref_seg0 = segment0_file_offset(reference)
    eu = load_regions()[0]
    eu_va = dict(eu['fixed'])
    eu_va.update({f's_c{i}': va for i, va in enumerate(eu['sites'])})
    for name, off, blob in built['patches']:
        length = BL_SITES.get(name, len(blob))
        got = eboot[seg0 + off:seg0 + off + length]
        want_off = eu_va[name] - SEG0_BASE if built['region']['name'] == 'JP' else off
        want = reference[ref_seg0 + want_off:ref_seg0 + want_off + length]
        if name == 's_f3' and built['region']['name'] == 'JP':
            if eboot[seg0 + off + 1] & 0xF8 != 0xF0:
                raise SystemExit(f'{label}: s_f3 is not a bl')
            continue
        if got != want:
            raise SystemExit(f'{label}: {name} at seg0+{off:#x} does not hold the stock bytes')
    span = range(built['cave_off'], built['data_off'] + len(built['data']))
    if built['cave_off'] < built['region']['seg0_size'] - 0x1000 and any(eboot[seg0 + o] for o in span):
        raise SystemExit(f'{label}: cave area at seg0+{built["cave_off"]:#x} is not zero')


def write_vita3k():
    os.makedirs(VITA3K_DIR, exist_ok=True)
    reference = open(os.environ['DIVA_EU_EBOOT'], 'rb').read()
    for region in load_regions():
        built = None
        for title in region['titles']:
            env = TITLE_ENV[title]
            path = os.environ.get(env)
            if not path:
                print(f'{title}: {env} not set, skipped')
                continue
            built = built or build_region(region, 'vita3k')
            check_against_reference(built, open(path, 'rb').read(), reference, title)
            lines = [f'0:0x{off:X} ' + ' '.join(f'0x{b:02X}' for b in blob)
                     for _, off, blob in built['patches']]
            lines += [f'0:0x{built["cave_off"]:X} ' + ' '.join(f'0x{b:02X}' for b in built['cave']),
                      f'0:0x{built["data_off"]:X} ' + ' '.join(f'0x{b:02X}' for b in built['data'])]
            open(os.path.join(VITA3K_DIR, title + '.txt'), 'w').write('\n'.join(lines) + '\n')
            print(f'{title}: wrote vita3k/{title}.txt')


def region_tables(built, eboot):
    name = built['region']['name']
    seg0 = segment0_file_offset(eboot)
    out = [f'static const patch_t PATCHES_{name}[] = {{']
    for _, offset, patched in built['patches']:
        assert len(patched) <= 8
        stock = eboot[seg0 + offset:seg0 + offset + len(patched)]
        out.append(f'\t{{ 0x{offset:X}, {len(patched)}, {{ {hex_bytes(stock, 8)} }}, {{ {hex_bytes(patched, 8)} }} }},')
    out += [
        '};',
        f'static const uint8_t CAVE_{name}[] = {{ {hex_bytes(built["cave"])} }};',
        f'static const cave_reloc_t RELOCS_{name}[] = {{',
    ]
    out += [f'\t{{ 0x{off:X}, {kind}, 0x{addend:X}u }},' for off, kind, addend in built['relocs']]
    out += [
        '};',
        f'static const uint8_t DATA_{name}[] = {{ {hex_bytes(built["data"])} }};',
    ]
    return out


def write_plugin():
    out = [
        'typedef struct { uint32_t offset; uint8_t size; uint8_t stock[8]; uint8_t patched[8]; } patch_t;',
        'enum { RELOC_SEG0 = 0, RELOC_SEG1 = 1, RELOC_DATA = 2 };',
        'typedef struct { uint16_t cave_off; uint8_t kind; uint32_t addend; } cave_reloc_t;',
        'typedef struct {',
        '\tconst patch_t *patches;',
        '\tsize_t patch_count;',
        '\tuint32_t cave_off;',
        '\tconst uint8_t *cave;',
        '\tsize_t cave_size;',
        '\tconst cave_reloc_t *relocs;',
        '\tsize_t reloc_count;',
        '\tconst uint8_t *data_init;',
        '\tsize_t data_size;',
        '} region_t;',
    ]
    rows = []
    summary = []
    for region in load_regions():
        path = os.environ.get(region['env'])
        if not path:
            print(f'{region["name"]}: {region["env"]} not set, skipped')
            continue
        built = build_region(region, 'plugin')
        out += region_tables(built, open(path, 'rb').read())
        n = region['name']
        rows.append(f'\t{{ PATCHES_{n}, sizeof(PATCHES_{n}) / sizeof(PATCHES_{n}[0]), 0x{built["cave_off"]:X}u, '
                    f'CAVE_{n}, sizeof(CAVE_{n}), RELOCS_{n}, sizeof(RELOCS_{n}) / sizeof(RELOCS_{n}[0]), '
                    f'DATA_{n}, sizeof(DATA_{n}) }},')
        summary.append(f'{n}: {len(built["patches"])} patches, {len(built["cave"])} byte cave at seg0+{built["cave_off"]:#x}, '
                       f'{len(built["relocs"])} relocations')
    out += ['static const region_t REGIONS[] = {'] + rows + ['};']
    open(OUT_HEADER, 'w').write('\n'.join(out) + '\n')
    print('\n'.join(summary))


def main():
    if TARGET == 'vita3k':
        write_vita3k()
    else:
        write_plugin()


main()

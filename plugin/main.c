#include <stdint.h>
#include <string.h>
#include <taihen.h>
#include <psp2/kernel/modulemgr.h>

#include "patches.h"

#define REGION_COUNT (sizeof(REGIONS) / sizeof(REGIONS[0]))
#define MAX_PATCHES 32
#define MAX_DATA 16
#define PAGE 0xFFFu

void _start() __attribute__((weak, alias("module_start")));

static uint8_t data[MAX_DATA] __attribute__((aligned(4)));
static SceUID cave_hook = -1;
static SceUID ids[MAX_PATCHES];
static size_t id_count;

static void undo(void) {
	for (size_t i = 0; i < id_count; i++) {
		if (ids[i] >= 0)
			taiInjectRelease(ids[i]);
		ids[i] = -1;
	}
	if (cave_hook >= 0)
		taiInjectRelease(cave_hook);
	cave_hook = -1;
}

static int is_stock(const region_t *r, uintptr_t seg0) {
	for (size_t i = 0; i < r->patch_count; i++)
		if (memcmp((const void *)(seg0 + r->patches[i].offset), r->patches[i].stock, r->patches[i].size))
			return 0;
	return 1;
}

static int cave_fits(const region_t *r, uintptr_t seg0, size_t memsz, uintptr_t end) {
	if (r->cave_off >= memsz)
		return seg0 + r->cave_off + r->cave_size <= end;
	const uint8_t *slot = (const uint8_t *)(seg0 + r->cave_off);
	for (size_t i = 0; i < r->cave_size; i++)
		if (slot[i])
			return 0;
	return 1;
}

static void fix_cave(const region_t *r, uint8_t *out, uintptr_t seg0, uintptr_t seg1) {
	memcpy(out, r->cave, r->cave_size);
	for (size_t i = 0; i < r->reloc_count; i++) {
		uint32_t base;
		switch (r->relocs[i].kind) {
		case RELOC_SEG0: base = seg0; break;
		case RELOC_SEG1: base = seg1; break;
		default:         base = (uint32_t)data; break;
		}
		uint32_t value = base + r->relocs[i].addend;
		memcpy(out + r->relocs[i].cave_off, &value, sizeof(value));
	}
}

static const region_t *find_region(uintptr_t seg0, size_t memsz, uintptr_t end) {
	for (size_t i = 0; i < REGION_COUNT; i++) {
		const region_t *r = &REGIONS[i];
		if (r->patch_count <= MAX_PATCHES && r->data_size <= MAX_DATA && cave_fits(r, seg0, memsz, end) && is_stock(r, seg0))
			return r;
	}
	return NULL;
}

int module_start(SceSize args, const void *argp) {
	tai_module_info_t info = { .size = sizeof(info) };
	if (taiGetModuleInfo(TAI_MAIN_MODULE, &info) < 0)
		return SCE_KERNEL_START_NO_RESIDENT;

	SceKernelModuleInfo mod = { .size = sizeof(mod) };
	if (sceKernelGetModuleInfo(info.modid, &mod) < 0)
		return SCE_KERNEL_START_NO_RESIDENT;

	uintptr_t seg0 = (uintptr_t)mod.segments[0].vaddr;
	uintptr_t seg1 = (uintptr_t)mod.segments[1].vaddr;
	uintptr_t end = (seg0 + mod.segments[0].memsz + PAGE) & ~(uintptr_t)PAGE;
	if (!seg0 || !seg1)
		return SCE_KERNEL_START_NO_RESIDENT;
	const region_t *r = find_region(seg0, mod.segments[0].memsz, end);
	if (!r)
		return SCE_KERNEL_START_NO_RESIDENT;

	memcpy(data, r->data_init, r->data_size);
	uint8_t cave[0x200];
	if (r->cave_size > sizeof(cave))
		return SCE_KERNEL_START_NO_RESIDENT;
	fix_cave(r, cave, seg0, seg1);

	memset(ids, -1, sizeof(ids));
	id_count = r->patch_count;
	cave_hook = taiInjectAbs((void *)(seg0 + r->cave_off), cave, r->cave_size);
	if (cave_hook < 0)
		return SCE_KERNEL_START_NO_RESIDENT;

	for (size_t i = 0; i < r->patch_count; i++) {
		ids[i] = taiInjectData(info.modid, 0, r->patches[i].offset, r->patches[i].patched, r->patches[i].size);
		if (ids[i] < 0) {
			undo();
			return SCE_KERNEL_START_NO_RESIDENT;
		}
	}
	return SCE_KERNEL_START_SUCCESS;
}

int module_stop(SceSize args, const void *argp) {
	undo();
	return SCE_KERNEL_STOP_SUCCESS;
}

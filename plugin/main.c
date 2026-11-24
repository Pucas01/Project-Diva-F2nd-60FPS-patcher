#include <stdint.h>
#include <string.h>
#include <taihen.h>
#include <psp2/kernel/modulemgr.h>

#include "patches.h"

#define PATCH_COUNT (sizeof(PATCHES) / sizeof(PATCHES[0]))
#define RELOC_COUNT (sizeof(CAVE_RELOCS) / sizeof(CAVE_RELOCS[0]))
#define PAGE 0xFFFu

void _start() __attribute__((weak, alias("module_start")));

static uint8_t data[sizeof(DATA_INIT)] __attribute__((aligned(4)));
static SceUID cave_hook = -1;
static SceUID ids[PATCH_COUNT];

static void undo(void) {
	for (size_t i = 0; i < PATCH_COUNT; i++) {
		if (ids[i] >= 0)
			taiInjectRelease(ids[i]);
		ids[i] = -1;
	}
	if (cave_hook >= 0)
		taiInjectRelease(cave_hook);
	cave_hook = -1;
}

static int is_stock(uintptr_t seg0) {
	for (size_t i = 0; i < PATCH_COUNT; i++)
		if (memcmp((const void *)(seg0 + PATCHES[i].offset), PATCHES[i].stock, PATCHES[i].size))
			return 0;
	return 1;
}

static void fix_cave(uint8_t *out, uintptr_t seg0, uintptr_t seg1) {
	memcpy(out, CAVE, sizeof(CAVE));
	for (size_t i = 0; i < RELOC_COUNT; i++) {
		uint32_t base;
		switch (CAVE_RELOCS[i].kind) {
		case RELOC_SEG0: base = seg0; break;
		case RELOC_SEG1: base = seg1; break;
		default:         base = (uint32_t)data; break;
		}
		uint32_t value = base + CAVE_RELOCS[i].addend;
		memcpy(out + CAVE_RELOCS[i].cave_off, &value, sizeof(value));
	}
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
	if (CAVE_OFF < mod.segments[0].memsz || seg0 + CAVE_OFF + sizeof(CAVE) > end)
		return SCE_KERNEL_START_NO_RESIDENT;
	if (!is_stock(seg0))
		return SCE_KERNEL_START_NO_RESIDENT;

	memcpy(data, DATA_INIT, sizeof(DATA_INIT));
	uint8_t cave[sizeof(CAVE)];
	fix_cave(cave, seg0, seg1);

	memset(ids, -1, sizeof(ids));
	cave_hook = taiInjectAbs((void *)(seg0 + CAVE_OFF), cave, sizeof(cave));
	if (cave_hook < 0)
		return SCE_KERNEL_START_NO_RESIDENT;

	for (size_t i = 0; i < PATCH_COUNT; i++) {
		ids[i] = taiInjectData(info.modid, 0, PATCHES[i].offset, PATCHES[i].patched, PATCHES[i].size);
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

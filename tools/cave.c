#include <stdint.h>

#define BTN_SELECT 0x001
#define BTN_L      0x100
#define BTN_R      0x200
#define HOTKEY     (BTN_SELECT | BTN_L | BTN_R)

#define STEP_30 (1.0f / 30)
#define STEP_60 (1.0f / 60)

extern int pad_read(int port, void *data, int count) __attribute__((long_call));
extern int wait_vblanks(int count) __attribute__((long_call));
extern char vtable_diva_room[];
extern char vtable_menu_miku[];
extern char vtable_song[];
extern uint32_t frame_counter;
extern float fps;

struct task { uint32_t *vtable; };
struct node { struct node *next; struct node *prev; struct task *task; };
struct list { uint32_t pad; struct node *head; };
extern struct list *task_list;

struct state {
	uint8_t mode;
	uint8_t hotkey_held;
	uint8_t pad;
	uint8_t user_30;
	float step;
	float scale;
};

struct state state __attribute__((section(".dat"))) = {
	.step = STEP_60,
	.scale = 30.0f,
};

struct pad_data {
	uint64_t time;
	uint32_t buttons;
	uint8_t rest[20];
};

static int task_running(const char *vtable) {
	if (!task_list)
		return 0;
	struct node *end = task_list->head;
	for (struct node *n = end->next; n != end; n = n->next)
		if (n->task && (char *)n->task->vtable == vtable)
			return 1;
	return 0;
}

int hook(void) {
	struct pad_data pad;
	pad_read(0, &pad, 1);

	int pressed = (pad.buttons & HOTKEY) == HOTKEY;
	if (pressed && !state.hotkey_held)
		state.user_30 ^= 1;
	state.hotkey_held = pressed;

	uint8_t mode = state.user_30 | task_running(vtable_diva_room);
	if (mode != state.mode) {
		state.mode = mode;
		state.step = mode ? STEP_30 : STEP_60;
		state.scale = mode ? 60.0f : 30.0f;
	}
	return wait_vblanks(state.mode + 1);
}

float get_scale(void) {
	return state.scale / fps;
}

float get_ui_scale(void) {
	return 60.0f / fps;
}

uint32_t tick_gate(uint32_t value, struct task *task) {
	if (state.mode || (char *)task->vtable != vtable_menu_miku)
		return value;
	if (!(frame_counter & 1))
		return value;
	return task_running(vtable_song) ? value : 0;
}

uint32_t held_gate(const uint8_t *pad, uint32_t key, int set) {
	if (!state.mode && (frame_counter & 1))
		return 0;
	if ((uint32_t)set > 1 || key >= 0x67)
		return 0;
	const uint16_t *bits = (const uint16_t *)(pad + set * 0xE + (key >> 4) * 2 + 0x54);
	return (*bits >> (key & 0xF)) & 1;
}

union bits { float f; uint32_t u; };

uint32_t tween_step(const uint32_t *tween) {
	union bits r;
	if (state.mode)
		r.f = STEP_30;
	else if (!(tween[7] & 0x10000))
		r.f = state.step;
	else if (frame_counter & 1)
		r.f = 0;
	else
		r.f = STEP_30;
	return r.u;
}

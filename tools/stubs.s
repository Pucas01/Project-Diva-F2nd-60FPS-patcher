	.syntax unified
	.thumb

	.macro shim reg
	.balign 2
	.thumb_func
	.global shim_r\reg
shim_r\reg:
	ldr	r\reg, =state
	ldr	r\reg, [r\reg, #4]
	bx	lr
	.ltorg
	.endm

	shim 0
	shim 1
	shim 3

	.balign 2
	.thumb_func
	.global tick_gate_stub
tick_gate_stub:
	push	{r1, r2, r3, r4, r12, lr}
	ldr	r0, [sp, #0x4c]
	mov	r1, r4
	bl	tick_gate
	pop	{r1, r2, r3, r4, r12, lr}
	cmp	r0, #0
	bx	lr

	.balign 2
	.thumb_func
	.global tween_stub
tween_stub:
	push	{r1, r2, r3, r4, r12, lr}
	mov	r0, r4
	bl	tween_step
	pop	{r1, r2, r3, r4, r12, lr}
	bx	lr

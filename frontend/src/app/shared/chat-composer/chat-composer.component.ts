import { ChangeDetectionStrategy, Component, computed, effect, ElementRef, inject, input, signal, untracked, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { KbqFormFieldModule } from '@koobiq/components/form-field';
import { KbqTextareaModule } from '@koobiq/components/textarea';
import { UiStateService } from '../../core/state/ui-state.service';
import { composerState, isSendShortcut } from './send-action';
import { SendButtonComponent } from './send-button.component';

/**
 * Message input with the send button inside (prototype `.v-inputbox`). The draft lives in sessionStorage per chat.
 * `submit` performs the request and resolves `true` on success; the button shows progress until then and the draft is
 * cleared only after success. Ctrl/⌘ Enter sends. Content projected below is the composer bar (agent, model).
 */
@Component({
    selector: 'v-chat-composer',
    imports: [FormsModule, KbqFormFieldModule, KbqTextareaModule, SendButtonComponent],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <div class="v-inputbox">
            <kbq-form-field>
                <textarea
                    #area
                    kbqTextarea
                    [canGrow]="false"
                    rows="2"
                    class="v-inputbox__area"
                    [placeholder]="placeholder()"
                    [attr.aria-label]="placeholder()"
                    [disabled]="state().locked"
                    [ngModel]="draft()"
                    (ngModelChange)="onDraft($event)"
                    (keydown)="onKeydown($event)"
                ></textarea>
            </kbq-form-field>
            <v-send-button
                class="v-inputbox__send"
                [label]="label()"
                [disabled]="state().disabled"
                [sending]="sending()"
                [reason]="state().reason"
                (pressed)="send()"
                >{{ label() }}</v-send-button
            >
        </div>
        <ng-content />
    `,
    styles: `
        :host { display: block; }
        .v-inputbox { position: relative; }
        .v-inputbox kbq-form-field { display: block; width: 100%; }
        .v-inputbox__area { display: block; width: 100%; resize: none; padding-bottom: calc(var(--kbq-size-3xl) + var(--kbq-size-m)); box-sizing: border-box; }
        .v-inputbox__send { position: absolute; right: var(--kbq-size-xxs); bottom: var(--kbq-size-xxs); z-index: 4; } /* above a focused kbq-form-field (z-index 3) */
    `,
})
export class ChatComposerComponent {
    /** sessionStorage draft key, usually the chat id. */
    readonly draftKey = input.required<string>();
    readonly placeholder = input.required<string>();
    readonly label = input.required<string>();
    readonly busy = input(false);
    readonly busyReason = input('');
    readonly emptyReason = input('');
    readonly submit = input.required<(text: string) => Promise<boolean>>();

    private readonly ui = inject(UiStateService);
    private readonly area = viewChild<ElementRef<HTMLTextAreaElement>>('area');

    protected readonly draft = signal('');
    protected readonly sending = signal(false);
    protected readonly state = computed(() =>
        composerState({
            busy: this.busy(),
            busyReason: this.busyReason(),
            emptyReason: this.emptyReason(),
            draft: this.draft(),
            sending: this.sending(),
        }),
    );

    constructor() {
        effect(() => {
            const key = this.draftKey();
            untracked(() => this.draft.set(this.ui.readDraft(key)));
        });
    }

    focus(): void {
        this.area()?.nativeElement.focus();
    }

    protected onDraft(text: string): void {
        this.draft.set(text);
        this.ui.writeDraft(this.draftKey(), text);
    }

    protected onKeydown(event: KeyboardEvent): void {
        if (!isSendShortcut(event)) return;
        event.preventDefault();
        void this.send();
    }

    protected async send(): Promise<void> {
        if (this.state().disabled) return;
        const text = this.draft().trim();
        const key = this.draftKey();
        this.sending.set(true);
        try {
            const ok = await this.submit()(text);
            if (ok) {
                this.ui.clearDraft(key);
                if (this.draftKey() === key) this.draft.set('');
            }
        } finally {
            this.sending.set(false);
        }
    }
}

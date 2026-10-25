import { ChangeDetectionStrategy, Component, effect, input, model } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { KbqFormFieldModule } from '@koobiq/components/form-field';
import { KbqIconModule } from '@koobiq/components/icon';
import { KbqInputModule } from '@koobiq/components/input';

/**
 * Search box over a table: magnifier, cleaner, client-side search from the first character (SPEC §12).
 * `[(value)]` holds the raw query; filtering is done by the screen with `core/search`.
 */
@Component({
    selector: 'v-admin-search',
    imports: [ReactiveFormsModule, KbqFormFieldModule, KbqInputModule, KbqIconModule],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <kbq-form-field>
            <i kbqPrefix kbq-icon="kbq-magnifying-glass_16"></i>
            <input
                kbqInput
                type="search"
                [formControl]="control"
                [placeholder]="placeholder()"
                [attr.aria-label]="placeholder()"
                autocomplete="off"
            />
            <kbq-cleaner />
        </kbq-form-field>
    `,
    styles: `
        :host {
            display: block;
            width: 320px;
            max-width: 100%;
        }
        input[type='search']::-webkit-search-cancel-button {
            display: none;
        }
    `,
})
export class AdminSearchComponent {
    readonly placeholder = input('');
    readonly value = model('');
    protected readonly control = new FormControl('', { nonNullable: true });

    constructor() {
        this.control.valueChanges.pipe(takeUntilDestroyed()).subscribe((v) => this.value.set(v ?? ''));
        effect(() => {
            const v = this.value();
            if (v !== this.control.value) this.control.setValue(v, { emitEvent: false });
        });
    }
}

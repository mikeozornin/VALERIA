import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { appConfig } from './app/app.config';
import { rewriteLegacyHash } from './app/core/guards/legacy-hash';

// Old Mattermost links (`#/consultations/:id`, `#/tasks/runs/:id`) become path routes before the router starts.
rewriteLegacyHash();

bootstrapApplication(App, appConfig).catch((err) => console.error(err));

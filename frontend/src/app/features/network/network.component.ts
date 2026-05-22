import { Component } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'cs-router',
  standalone: true,
  imports: [MatIconModule],
  template: `
    <div style="padding:24px">
      <h2><mat-icon>router</mat-icon> Network</h2>
      <p>Wird in Phase 2+ implementiert.</p>
    </div>
  `
})
export class NetworkComponent {}

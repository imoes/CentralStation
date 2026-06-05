import { Component } from '@angular/core';
import { FormBuilder, Validators, ReactiveFormsModule } from '@angular/forms';
import { Router, ActivatedRoute } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { AuthService } from '../../../core/auth/auth.service';
import { environment } from '../../../../environments/environment';

@Component({
  selector: 'cs-login',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule,
    MatButtonModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="login-container">
      <mat-card class="login-card">
        <mat-card-header>
          <mat-card-title>CentralStation</mat-card-title>
          <mat-card-subtitle>IT Operations Dashboard</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <form [formGroup]="form" (ngSubmit)="onSubmit()">
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>E-Mail</mat-label>
              <input matInput type="email" formControlName="email" autocomplete="email" name="email" id="login-email">
            </mat-form-field>
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Passwort</mat-label>
              <input matInput type="password" formControlName="password" autocomplete="current-password" name="password" id="login-password">
            </mat-form-field>
            @if (error) {
              <p class="error-msg">{{ error }}</p>
            }
            <button mat-raised-button color="primary" type="submit"
                    [disabled]="form.invalid || loading" class="full-width">
              @if (loading) {
                <mat-spinner diameter="20"></mat-spinner>
              } @else {
                Anmelden
              }
            </button>
          </form>
        </mat-card-content>
      </mat-card>
    </div>
  `,
  styles: [`
    .login-container {
      display: flex;
      justify-content: center;
      align-items: center;
      height: 100vh;
      background: var(--mat-sys-surface-container);
    }
    .login-card { width: 360px; padding: 16px; }
    .full-width { width: 100%; margin-bottom: 12px; }
    .error-msg { color: var(--mat-sys-error); margin-bottom: 8px; }
    button mat-spinner { display: inline-block; }
  `],
})
export class LoginComponent {
  loading = false;
  error = '';
  form: ReturnType<FormBuilder['group']>;

  constructor(
    private fb: FormBuilder,
    private auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
    private http: HttpClient,
  ) {
    this.form = this.fb.group({
      email: ['', [Validators.required, Validators.email]],
      password: ['', Validators.required],
    });
  }

  onSubmit() {
    if (this.form.invalid) return;
    this.loading = true;
    this.error = '';
    const { email, password } = this.form.value;

    const returnUrl = this.route.snapshot.queryParamMap.get('returnUrl');

    this.auth.login(email!, password!).subscribe({
      next: () => {
        // Honour returnUrl (e.g. a cockpit window that lost its session) over the default.
        if (returnUrl) {
          this.router.navigateByUrl(returnUrl);
          return;
        }
        this.http.get<any>(`${environment.apiUrl}/preferences`).subscribe({
          next: prefs => this.router.navigate([prefs?.setup_completed === false ? '/setup' : '/dashboard']),
          error: () => this.router.navigate(['/dashboard']),
        });
      },
      error: () => {
        this.error = 'Ungültige Anmeldedaten';
        this.loading = false;
      },
    });
  }
}

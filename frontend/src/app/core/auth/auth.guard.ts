import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';
import { Role } from '../models/user.model';

export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (auth.isLoggedIn()) return true;

  // No in-memory token (fresh window via window.open, or full reload) —
  // attempt a silent refresh using the HttpOnly cookie before bouncing to login.
  return auth.ensureAuthenticated().pipe(
    map(ok => ok
      ? true
      : router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } })),
  );
};

export function roleGuard(...allowedRoles: Role[]): CanActivateFn {
  return () => {
    const auth = inject(AuthService);
    const router = inject(Router);

    const role = auth.userRole();
    if (role && allowedRoles.includes(role as Role)) return true;
    return router.createUrlTree(['/dashboard']);
  };
}

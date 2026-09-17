import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';
import { Role } from '../models/user.model';

export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  // A stored token is not enough for role-protected routes: wait until the user
  // profile is available before the next guard evaluates it.
  return auth.ensureAuthenticated().pipe(
    map(ok => ok
      ? true
      : router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } })),
  );
};

export function roleGuard(...allowedRoles: Role[]): CanActivateFn {
  return (_route, state) => {
    const auth = inject(AuthService);
    const router = inject(Router);

    return auth.ensureAuthenticated().pipe(map(ok => {
      if (!ok) return router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
      const role = auth.userRole();
      return role && allowedRoles.includes(role as Role)
        ? true
        : router.createUrlTree(['/dashboard']);
    }));
  };
}

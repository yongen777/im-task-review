import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { LoginPage } from './pages/LoginPage.tsx';
import { ChangePasswordPage } from './pages/ChangePasswordPage.tsx';
import { api, setCsrfToken } from './lib/api.ts';
import { ToastProvider } from './lib/toast.tsx';
import './index.css';

interface MeResponse {
  ok: boolean;
  username: string;
  must_change_password: boolean;
  csrf_token: string;
}

type BootState =
  | { phase: 'loading' }
  | { phase: 'unauthed' }
  | { phase: 'authed'; me: MeResponse };

const RootRouter = () => {
  const path = window.location.pathname;
  const [state, setState] = useState<BootState>(() =>
    path === '/login' ? { phase: 'unauthed' } : { phase: 'loading' },
  );

  useEffect(() => {
    if (path === '/login') return;
    api
      .get<MeResponse>('/api/admin/me')
      .then((me) => {
        setCsrfToken(me.csrf_token);
        setState({ phase: 'authed', me });
      })
      .catch(() => {
        setState({ phase: 'unauthed' });
      });
  }, [path]);

  if (path === '/login' || state.phase === 'unauthed') {
    return <LoginPage />;
  }
  if (state.phase === 'loading') {
    return <div className="min-h-screen w-full bg-[#f7f8fa]" />;
  }
  if (path === '/change-password' || state.me.must_change_password) {
    return <ChangePasswordPage onChanged={() => setState({ phase: 'unauthed' })} />;
  }
  return (
    <App
      me={state.me}
      onLoggedOut={() => {
        setCsrfToken('');
        setState({ phase: 'unauthed' });
      }}
    />
  );
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <RootRouter />
    </ToastProvider>
  </StrictMode>,
);

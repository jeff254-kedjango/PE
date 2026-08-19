import React, { useState, useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import Icon from '../components/ui/Icon';
import type { LoginMethod } from '../types/auth';
import PageMeta from '../components/ui/PageMeta';
import { useAfterAuthRedirect } from '../hooks/useAfterAuthRedirect';
import './LoginPage.css';

const RESEND_COOLDOWN = 60; // seconds

const LoginPage: React.FC = () => {
  const { search } = useLocation();
  const { login, verifyOtp, resendOtp, loginWithGoogle, loginWithApple, isLoading } = useAuth();
  const { toast } = useToast();

  // After a successful sign-in: home page normally, or open InSAR deep-linked when the
  // user arrived via ?next=insar (InSAR is free but login-required — see api/insar.ts).
  const goAfterLogin = useAfterAuthRedirect();

  const [method, setMethod] = useState<LoginMethod>('phone');
  const [phone, setPhone] = useState('');
  const [otpSent, setOtpSent] = useState(false);
  const [otp, setOtp] = useState('');
  const [devOtp, setDevOtp] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startCountdown = () => {
    setCountdown(RESEND_COOLDOWN);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timerRef.current!);
          timerRef.current = null;
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  };

  useEffect(() => {
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!phone || phone.length < 9) {
      setError('Enter a valid phone number');
      return;
    }

    try {
      const result = await login({ phone });
      if (result?.otp_sent) {
        setOtpSent(true);
        setDevOtp(result.otp || '');
        startCountdown();
        toast.success('OTP sent!');
      }
    } catch (err: any) {
      setError(err.message || 'Failed to send OTP');
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (otp.length < 6) {
      setError('Enter the full 6-digit OTP code');
      return;
    }

    try {
      await verifyOtp({ phone, otp });
      toast.success('Login successful');
      await goAfterLogin();
    } catch (err: any) {
      setError(err.message || 'Invalid OTP');
    }
  };

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!email || !password) {
      setError('Fill in all fields');
      return;
    }

    try {
      await login({ email, password });
      toast.success('Login successful');
      await goAfterLogin();
    } catch (err: any) {
      setError(err.message || 'Login failed');
    }
  };

  const handleSocialLogin = async (provider: 'google' | 'apple') => {
    try {
      if (provider === 'google') await loginWithGoogle();
      else await loginWithApple();
    } catch (err: any) {
      setError(err.message || `${provider} login failed`);
    }
  };

  return (
    <div className="login-page">
      <PageMeta title="Sign In" description="Sign in to your Weespas account to save properties, contact agents, and more." />
      <div className="login-card">
        {/* Header */}
        <div className="login-header">
          <Link to="/" className="login-logo">weespas</Link>
          <h1>Welcome back</h1>
          <p>Sign in to save properties, contact agents, and more.</p>
        </div>

        {/* Method Toggle */}
        <div className="login-toggle">
          <button
            type="button"
            className={`login-toggle__btn ${method === 'phone' ? 'login-toggle__btn--active' : ''}`}
            onClick={() => { setMethod('phone'); setError(''); setOtpSent(false); }}
          >
            <Icon name="phone" size={16} />
            Phone
          </button>
          <button
            type="button"
            className={`login-toggle__btn ${method === 'email' ? 'login-toggle__btn--active' : ''}`}
            onClick={() => { setMethod('email'); setError(''); }}
          >
            <Icon name="user" size={16} />
            Email
          </button>
        </div>

        {/* Error */}
        {error && <div className="login-error">{error}</div>}

        {/* Phone + OTP Flow */}
        {method === 'phone' && !otpSent && (
          <form className="login-form" onSubmit={handleSendOtp}>
            <label className="login-field">
              <span className="login-field__label">Phone number</span>
              <div className="login-field__input-wrap">
                <span className="login-field__prefix">+254</span>
                <input
                  type="tel"
                  className="login-field__input login-field__input--prefixed"
                  placeholder="712 345 678"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value.replace(/\D/g, ''))}
                  maxLength={10}
                  autoFocus
                />
              </div>
            </label>
            <button type="submit" className="login-submit" disabled={isLoading}>
              {isLoading ? 'Sending...' : 'Send OTP'}
            </button>
          </form>
        )}

        {/* OTP Verification */}
        {method === 'phone' && otpSent && (
          <form className="login-form" onSubmit={handleVerifyOtp}>
            <p className="login-otp-info">
              We sent a code to <strong>+254 {phone}</strong>
            </p>
            <label className="login-field">
              <span className="login-field__label">Enter OTP</span>
              <input
                type="text"
                className="login-field__input login-field__input--otp"
                placeholder="0000"
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                maxLength={6}
                autoFocus
                inputMode="numeric"
              />
            </label>
            <button type="submit" className="login-submit" disabled={isLoading}>
              {isLoading ? 'Verifying...' : 'Verify & Sign In'}
            </button>
            <button
              type="button"
              className="login-resend"
              disabled={countdown > 0 || isLoading}
              onClick={async () => {
                setError('');
                try {
                  const result = await resendOtp(phone);
                  setDevOtp(result.otp || '');
                  setOtp('');
                  startCountdown();
                  toast.success('OTP resent!');
                } catch (err: any) {
                  setError(err.message || 'Failed to resend OTP');
                }
              }}
            >
              {countdown > 0
                ? `Resend in ${countdown}s`
                : "Didn't receive it? Resend"}
            </button>
            {devOtp && (
              <div className="login-dev-otp">
                DEV: {devOtp}
              </div>
            )}
          </form>
        )}

        {/* Email + Password Flow */}
        {method === 'email' && (
          <form className="login-form" onSubmit={handleEmailLogin}>
            <label className="login-field">
              <span className="login-field__label">Email</span>
              <input
                type="email"
                className="login-field__input"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
              />
            </label>
            <label className="login-field">
              <span className="login-field__label">Password</span>
              <div className="login-field__input-wrap">
                <input
                  type={showPassword ? 'text' : 'password'}
                  className="login-field__input"
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="login-field__toggle"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </label>
            <button type="submit" className="login-submit" disabled={isLoading}>
              {isLoading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
        )}

        {/* Divider */}
        <div className="login-divider">
          <span>or continue with</span>
        </div>

        {/* Social Login */}
        <div className="login-social">
          <button
            type="button"
            className="login-social__btn login-social__btn--google"
            onClick={() => handleSocialLogin('google')}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11.96 11.96 0 0 0 1 12c0 1.92.44 3.73 1.18 5.33z" fill="#FBBC05"/>
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
            </svg>
            Google
          </button>
          <button
            type="button"
            className="login-social__btn login-social__btn--apple"
            onClick={() => handleSocialLogin('apple')}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M17.05 20.28c-.98.95-2.05.88-3.08.4-1.09-.5-2.08-.48-3.24 0-1.44.62-2.2.44-3.06-.4C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.32 2.32-2.12 4.53-3.74 4.25z"/>
            </svg>
            Apple
          </button>
        </div>

        {/* Sign up link */}
        <p className="login-footer">
          Don't have an account? <Link to={`/register${search}`} className="login-footer__link">Sign Up</Link>
        </p>
      </div>
    </div>
  );
};

export default LoginPage;

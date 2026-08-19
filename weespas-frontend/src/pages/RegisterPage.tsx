import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import Icon from '../components/ui/Icon';
import PageMeta from '../components/ui/PageMeta';
import { useAfterAuthRedirect } from '../hooks/useAfterAuthRedirect';
import './RegisterPage.css';

const RegisterPage: React.FC = () => {
  const { search } = useLocation();
  const { register, loginWithGoogle, loginWithApple, isLoading } = useAuth();
  const { toast } = useToast();

  // Home page after sign-up, or resume into InSAR when arriving via ?next=insar.
  const goAfterRegister = useAfterAuthRedirect();

  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [generalError, setGeneralError] = useState('');

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!name.trim()) {
      newErrors.name = 'Full name is required';
    }

    if (!phone || phone.length < 9) {
      newErrors.phone = 'Enter a valid Kenyan phone number';
    }

    if (!email.trim()) {
      newErrors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      newErrors.email = 'Enter a valid email address';
    }

    if (!password) {
      newErrors.password = 'Password is required';
    } else if (password.length < 8) {
      newErrors.password = 'Password must be at least 8 characters';
    }

    if (!agreedToTerms) {
      newErrors.terms = 'You must agree to the terms';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const isFormValid =
    name.trim() &&
    phone.length >= 9 &&
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) &&
    password.length >= 8 &&
    agreedToTerms;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setGeneralError('');

    if (!validate()) return;

    try {
      await register({
        name: name.trim(),
        phone: `+254${phone}`,
        email: email.trim(),
        password,
      });
      toast.success('Account created!');
      await goAfterRegister();
    } catch (err: any) {
      setGeneralError(err.message || 'Registration failed. Please try again.');
    }
  };

  const handleSocialLogin = async (provider: 'google' | 'apple') => {
    try {
      if (provider === 'google') await loginWithGoogle();
      else await loginWithApple();
    } catch (err: any) {
      setGeneralError(err.message || `${provider} sign up failed`);
    }
  };

  return (
    <div className="register-page">
      <PageMeta title="Create Account" description="Join Weespas to find your perfect property in Kenya. Browse listings, save favorites, and connect with agents." />
      <div className="register-card">
        {/* Header */}
        <div className="register-header">
          <Link to="/" className="register-logo">weespas</Link>
          <h1>Create your account</h1>
          <p>Join thousands of Kenyans finding their perfect home.</p>
        </div>

        {/* General Error */}
        {generalError && <div className="register-error">{generalError}</div>}

        {/* Registration Form */}
        <form className="register-form" onSubmit={handleSubmit} noValidate>
          {/* Full Name */}
          <label className="register-field">
            <span className="register-field__label">Full Name</span>
            <div className="register-field__input-wrap">
              <Icon name="user" size={18} />
              <input
                type="text"
                className={`register-field__input register-field__input--icon ${errors.name ? 'register-field__input--error' : ''}`}
                placeholder="John Kamau"
                value={name}
                onChange={(e) => { setName(e.target.value); setErrors((prev) => ({ ...prev, name: '' })); }}
                autoFocus
              />
            </div>
            {errors.name && <span className="register-field__error">{errors.name}</span>}
          </label>

          {/* Phone Number */}
          <label className="register-field">
            <span className="register-field__label">Phone Number</span>
            <div className="register-field__input-wrap">
              <span className="register-field__prefix">+254</span>
              <input
                type="tel"
                className={`register-field__input register-field__input--prefixed ${errors.phone ? 'register-field__input--error' : ''}`}
                placeholder="712 345 678"
                value={phone}
                onChange={(e) => { setPhone(e.target.value.replace(/\D/g, '')); setErrors((prev) => ({ ...prev, phone: '' })); }}
                maxLength={10}
                inputMode="numeric"
              />
            </div>
            {errors.phone && <span className="register-field__error">{errors.phone}</span>}
          </label>

          {/* Email */}
          <label className="register-field">
            <span className="register-field__label">Email</span>
            <input
              type="email"
              className={`register-field__input ${errors.email ? 'register-field__input--error' : ''}`}
              placeholder="you@example.com"
              value={email}
              onChange={(e) => { setEmail(e.target.value); setErrors((prev) => ({ ...prev, email: '' })); }}
            />
            {errors.email && <span className="register-field__error">{errors.email}</span>}
          </label>

          {/* Password */}
          <label className="register-field">
            <span className="register-field__label">Password</span>
            <div className="register-field__input-wrap">
              <input
                type={showPassword ? 'text' : 'password'}
                className={`register-field__input ${errors.password ? 'register-field__input--error' : ''}`}
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => { setPassword(e.target.value); setErrors((prev) => ({ ...prev, password: '' })); }}
              />
              <button
                type="button"
                className="register-field__toggle"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
            {errors.password && <span className="register-field__error">{errors.password}</span>}
            {password && password.length >= 8 && (
              <span className="register-field__hint register-field__hint--valid">Strong enough</span>
            )}
          </label>

          {/* Terms & Conditions */}
          <label className={`register-terms ${errors.terms ? 'register-terms--error' : ''}`}>
            <input
              type="checkbox"
              className="register-terms__checkbox"
              checked={agreedToTerms}
              onChange={(e) => { setAgreedToTerms(e.target.checked); setErrors((prev) => ({ ...prev, terms: '' })); }}
            />
            <span className="register-terms__text">
              I agree to the <a href="/terms" className="register-terms__link">Terms of Service</a> and <a href="/privacy" className="register-terms__link">Privacy Policy</a>
            </span>
          </label>
          {errors.terms && <span className="register-field__error">{errors.terms}</span>}

          {/* Submit */}
          <button type="submit" className="register-submit" disabled={!isFormValid || isLoading}>
            {isLoading ? (
              <span className="register-submit__loading">
                <span className="register-submit__spinner" />
                Creating account...
              </span>
            ) : (
              'Create Account'
            )}
          </button>
        </form>

        {/* Divider */}
        <div className="register-divider">
          <span>or sign up with</span>
        </div>

        {/* Social Login */}
        <div className="register-social">
          <button
            type="button"
            className="register-social__btn register-social__btn--google"
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
            className="register-social__btn register-social__btn--apple"
            onClick={() => handleSocialLogin('apple')}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M17.05 20.28c-.98.95-2.05.88-3.08.4-1.09-.5-2.08-.48-3.24 0-1.44.62-2.2.44-3.06-.4C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.32 2.32-2.12 4.53-3.74 4.25z"/>
            </svg>
            Apple
          </button>
        </div>

        {/* Login link */}
        <p className="register-footer">
          Already have an account? <Link to={`/login${search}`} className="register-footer__link">Sign In</Link>
        </p>
      </div>
    </div>
  );
};

export default RegisterPage;

/* ==========================================================================
   FOOTER — Clean 3-column layout + contact form
   ========================================================================== */

import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { submitContactForm } from '../../api/contact';
import CustomSelect from '../ui/CustomSelect';
import Icon from '../ui/Icon';
import './Footer.css';

const Footer: React.FC = () => {
  const [formData, setFormData] = useState({
    inquiryPurpose: '',
    description: '',
    fullName: '',
    email: '',
    organization: '',
    phone: '',
    message: '',
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStatus, setSubmitStatus] = useState<'idle' | 'success' | 'error'>('idle');

  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => {
    setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setSubmitStatus('idle');

    try {
      await submitContactForm({
        inquiry_purpose: formData.inquiryPurpose,
        description: formData.description,
        full_name: formData.fullName || undefined,
        email: formData.email || undefined,
        organization: formData.organization || undefined,
        phone: formData.phone || undefined,
        message: formData.message,
      });
      setSubmitStatus('success');
      setFormData({
        inquiryPurpose: '',
        description: '',
        fullName: '',
        email: '',
        organization: '',
        phone: '',
        message: '',
      });
    } catch {
      setSubmitStatus('error');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <footer className="footer">
      <div className="footer__inner container">
        <div className="footer__grid">
          {/* Brand column */}
          <div className="footer__col">
            <h3 className="footer__logo">
              weespas
              <span className="brand-mark">
                <Icon name="crosshair" />
              </span>
            </h3>
            <p className="footer__tagline">
              Find. Trade. Move - The location-first network connecting verified spaces, local commerce, and seamless mobility across Kenya.
            </p>
            <div className="footer__socials">
              <a href="#" className="footer__social" aria-label="Facebook">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M22 12c0-5.52-4.48-10-10-10S2 6.48 2 12c0 4.99 3.66 9.13 8.44 9.88v-7H8.9v-2.88h1.53V9.8c0-1.51.9-2.34 2.28-2.34.66 0 1.35.12 1.35.12v1.48h-.76c-.75 0-.98.47-.98.95v1.14h1.67l-.27 2.88h-1.4V21.9C18.34 21.13 22 16.99 22 12z"/></svg>
              </a>
              <a href="#" className="footer__social" aria-label="X (Twitter)">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
              </a>
              <a href="#" className="footer__social" aria-label="Instagram">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M7.8 2h8.4C19.4 2 22 4.6 22 7.8v8.4a5.8 5.8 0 0 1-5.8 5.8H7.8C4.6 22 2 19.4 2 16.2V7.8A5.8 5.8 0 0 1 7.8 2m-.2 2A3.6 3.6 0 0 0 4 7.6v8.8C4 18.39 5.61 20 7.6 20h8.8a3.6 3.6 0 0 0 3.6-3.6V7.6C20 5.61 18.39 4 16.4 4H7.6m9.65 1.5a1.25 1.25 0 1 1 0 2.5 1.25 1.25 0 0 1 0-2.5M12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10m0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>
              </a>
              <a href="#" className="footer__social" aria-label="TikTok">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>
              </a>
            </div>
          </div>

          {/* Quick Links */}
          <div className="footer__col">
            <h4 className="footer__heading">Quick Links</h4>
            <nav className="footer__nav">
              <Link to="/" className="footer__link">Home</Link>
              <Link to="/" className="footer__link">Properties</Link>
              <Link to="/login" className="footer__link">Login</Link>
              <Link to="/register" className="footer__link">Sign Up</Link>
            </nav>
          </div>

          {/* Contact */}
          <div className="footer__col">
            <h4 className="footer__heading">Contact</h4>
            <nav className="footer__nav">
              <a href="tel:+254713083378" className="footer__link">+254 713 083 378</a>
              <a href="mailto:hello@weespas.com" className="footer__link">hello@weespas.com</a>
              <span className="footer__link">Nairobi, Kenya</span>
            </nav>
          </div>
        </div>

        {/* Contact Form */}
        <div className="footer__contact">
          <h2 id="contact-form" className="footer__contact-title">Or Let's Talk Now</h2>
          <Link to="/customer-care" className="footer__customer-care-link">
            <Icon name="supportAgent" size={18} />
            Customer Care
          </Link>

          {submitStatus === 'success' && (
            <div className="footer__form-success">
              Thank you! Your message has been sent. We'll get back to you soon.
            </div>
          )}
          {submitStatus === 'error' && (
            <div className="footer__form-error">
              Something went wrong. Please try again.
            </div>
          )}

          <form className="footer__form" onSubmit={handleSubmit}>
            <div className="footer__form-row">
              <div className="footer__field">
                <span className="footer__field-label">Inquiry Purpose<span className="footer__required">*</span></span>
                <div className="footer__custom-select-wrap">
                  <CustomSelect
                    options={[
                      { label: 'General Inquiry', value: 'general' },
                      { label: 'Property Inquiry', value: 'property' },
                      { label: 'Partnership', value: 'partnership' },
                      { label: 'Support', value: 'support' },
                    ]}
                    value={formData.inquiryPurpose}
                    onChange={(val) => setFormData((prev) => ({ ...prev, inquiryPurpose: val }))}
                    placeholder="Choose one option..."
                  />
                </div>
              </div>

              <div className="footer__field">
                <span className="footer__field-label">Description that fits you<span className="footer__required">*</span></span>
                <div className="footer__custom-select-wrap">
                  <CustomSelect
                    options={[
                      { label: 'Buyer', value: 'buyer' },
                      { label: 'Seller', value: 'seller' },
                      { label: 'Agent', value: 'agent' },
                      { label: 'Investor', value: 'investor' },
                      { label: 'Other', value: 'other' },
                    ]}
                    value={formData.description}
                    onChange={(val) => setFormData((prev) => ({ ...prev, description: val }))}
                    placeholder="Choose one option..."
                  />
                </div>
              </div>
            </div>

            <div className="footer__form-row">
              <label className="footer__field">
                <span className="footer__field-label">Full Name</span>
                <div className="footer__input-wrap">
                  <svg className="footer__field-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                  <input
                    type="text"
                    name="fullName"
                    placeholder="Enter your full name..."
                    value={formData.fullName}
                    onChange={handleChange}
                  />
                </div>
              </label>

              <label className="footer__field">
                <span className="footer__field-label">Email Address</span>
                <div className="footer__input-wrap">
                  <svg className="footer__field-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
                  <input
                    type="email"
                    name="email"
                    placeholder="Enter your email address...."
                    value={formData.email}
                    onChange={handleChange}
                  />
                </div>
              </label>
            </div>

            <div className="footer__form-row">
              <label className="footer__field">
                <span className="footer__field-label">Organization</span>
                <div className="footer__input-wrap">
                  <svg className="footer__field-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
                  <input
                    type="text"
                    name="organization"
                    placeholder="Enter your organization..."
                    value={formData.organization}
                    onChange={handleChange}
                  />
                </div>
              </label>

              <label className="footer__field">
                <span className="footer__field-label">Phone Number</span>
                <div className="footer__input-wrap">
                  <svg className="footer__field-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
                  <input
                    type="tel"
                    name="phone"
                    placeholder="Enter your phone number..."
                    value={formData.phone}
                    onChange={handleChange}
                  />
                </div>
              </label>
            </div>

            <label className="footer__field footer__field--full">
              <span className="footer__field-label">Message<span className="footer__required">*</span></span>
              <div className="footer__input-wrap footer__input-wrap--textarea">
                <svg className="footer__field-icon footer__field-icon--textarea" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <textarea
                  name="message"
                  placeholder="Enter your message here..."
                  rows={4}
                  value={formData.message}
                  onChange={handleChange}
                  required
                />
              </div>
            </label>

            <div className="footer__form-actions">
              <button type="submit" className="footer__submit" disabled={isSubmitting}>
                {isSubmitting ? 'Submitting...' : 'Submit Form'}
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              </button>
            </div>
          </form>
        </div>

        {/* Bottom bar */}
        <div className="footer__bottom">
          <p>&copy; {new Date().getFullYear()} Weespas. All rights reserved.</p>
          <button type="button" className="footer__top-btn" onClick={scrollToTop}>
            Back to top
          </button>
        </div>
      </div>
    </footer>
  );
};

export default Footer;

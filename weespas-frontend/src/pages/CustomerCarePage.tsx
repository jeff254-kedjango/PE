import React from 'react';
import Icon from '../components/ui/Icon';
import PageMeta from '../components/ui/PageMeta';
import './CustomerCarePage.css';

const COMPANY_PHONE = '+254713083378';
const COMPANY_PHONE_CLEAN = '254713083378';

const faqs = [
  {
    q: 'How do I list my property on Weespas?',
    a: 'Create an account, then contact our team to get agent access. Once approved, you can list properties directly from your Agent Dashboard.',
  },
  {
    q: 'Is Weespas free to use for buyers and renters?',
    a: 'Yes! Browsing, saving, and contacting agents is completely free for all users.',
  },
  {
    q: 'How do I reset my password?',
    a: 'Go to the Login page and tap "Forgot Password". We\'ll send a reset link to your registered email or phone.',
  },
  {
    q: 'What areas does Weespas cover?',
    a: 'We currently cover properties across Nairobi, Mombasa, Kisumu, and other major Kenyan cities. We\'re expanding rapidly.',
  },
  {
    q: 'How can I verify if a property listing is genuine?',
    a: 'Look for the "Engineer Certified" badge on listings. You can also contact our support team to verify any listing before scheduling a visit.',
  },
  {
    q: 'How do I report a suspicious listing?',
    a: 'Tap the share icon on any property, then select "Report". Our team reviews all reports within 24 hours.',
  },
];

function whatsappUrl(phone: string, text: string) {
  return `https://wa.me/${phone.replace(/\D/g, '')}?text=${encodeURIComponent(text)}`;
}

const CustomerCarePage: React.FC = () => {
  const waUrl = whatsappUrl(COMPANY_PHONE_CLEAN, 'Hi Weespas, I need help with...');

  return (
    <div className="cc-page">
      <PageMeta
        title="Customer Care"
        description="24Hr Weespas customer support — call, message, or video-call our team for help with listings, accounts, or reports."
      />

      <div className="cc-hero">
        <div className="cc-hero__icon">
          <Icon name="supportAgent" size={48} />
        </div>
        <h1 className="cc-hero__title">Customer Care</h1>
        <p className="cc-hero__subtitle">
          24Hr support. Reach our team any time on the channel that works best for you.
        </p>
      </div>

      <div className="cc-content">
        {/* Primary contact details */}
        <section className="cc-call-card">
          <p className="cc-call-card__label">Call us directly</p>
          <a href={`tel:${COMPANY_PHONE}`} className="cc-call-card__number">{COMPANY_PHONE}</a>
          <p className="cc-call-card__note">Available 24 hours a day, 7 days a week.</p>

          <div className="cc-actions" role="group" aria-label="Contact options">
            <a
              href={`tel:${COMPANY_PHONE}`}
              className="cc-action cc-action--call"
              aria-label="Call Weespas customer care"
              title="Call"
            >
              <span className="cc-action__tooltip">Call</span>
              <Icon name="phone" size={22} />
            </a>
            <a
              href={waUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="cc-action cc-action--whatsapp"
              aria-label="WhatsApp Weespas customer care"
              title="WhatsApp"
            >
              <span className="cc-action__tooltip">WhatsApp</span>
              <Icon name="whatsapp" size={22} />
            </a>
            <button
              type="button"
              className="cc-action cc-action--video"
              aria-label="Video call Weespas customer care"
              title="Video Call"
              disabled
            >
              <span className="cc-action__tooltip">Video Call &mdash; Coming Soon</span>
              <Icon name="videoCall" size={22} />
            </button>
          </div>
        </section>

        {/* FAQ */}
        <section className="cc-faq">
          <h2 className="cc-faq__title">Frequently Asked Questions</h2>
          <div className="cc-faq__list">
            {faqs.map((faq, i) => (
              <details key={i} className="cc-faq__item">
                <summary className="cc-faq__question">
                  {faq.q}
                  <Icon name="chevronRight" size={16} className="cc-faq__chevron" />
                </summary>
                <p className="cc-faq__answer">{faq.a}</p>
              </details>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
};

export default CustomerCarePage;

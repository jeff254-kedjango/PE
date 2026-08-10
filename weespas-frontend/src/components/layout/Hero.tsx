/* ==========================================================================
   HERO — 100vh landing section
   White background, info left, map animation as ambient background layer
   behind content. Text search now lives in the global navbar search (which
   covers BOTH properties and commerce), so the hero no longer carries its own
   search bar — more hero design lands here later.
   ========================================================================== */

import React from 'react';
import HeroAnimation from './HeroAnimation';
import './Hero.css';

const Hero: React.FC = () => {
  /* Smooth-scroll to the property listings. The anchor (#after-hero) sits at the bottom of the
     hero, immediately above the listings grid. This is in-page (the hero only renders on home),
     so no router navigation is needed. */
  const scrollToProperties = () => {
    const el = document.getElementById('after-hero');
    if (el) el.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <section className="hero">
      {/* Animation — background layer (z-index 1) */}
      <div className="hero__animation">
        <HeroAnimation />
      </div>

      {/* Main content (z-index 2) */}
      <div className="hero__body container">
        <div className="hero__info">
          <span className="hero__eyebrow">Kenya's #1 Discovery Platform</span>
          <h1 className="hero__title">
            signals that <br /> matter
          </h1>
          <p className="hero__subtitle">
            Find. Trade. Move - 
            <br /><span>The location-first network connecting verified spaces, local commerce, and seamless mobility across Kenya.</span>
          </p>

          {/* Stats */}
          <div className="hero__stats">
            <div className="hero__stat">
              <strong>12M</strong>
              <span>Listings</span>
            </div>
            <div className="hero__stat-divider" />
            <div className="hero__stat">
              <strong>100+</strong>
              <span>Cities</span>
            </div>
            <div className="hero__stat-divider" />
            <div className="hero__stat">
              <strong>10K</strong>
              <span>Agents</span>
            </div>
          </div>
        </div>
      </div>

      {/* Scroll hint (z-index 2) — clickable, jumps to the listings. Styled as a plain hint (NOT a
          link): no underline/link colour, just a subtle hover reaction. It's a real <button> for
          keyboard + screen-reader access. */}
      <button type="button" className="hero__scroll-hint" onClick={scrollToProperties} aria-label="Scroll down to properties">
        <span className="hero__scroll-text">Scroll Down</span>
        <div className="hero__scroll-line" />
      </button>

      {/* Scroll anchor */}
      <div id="after-hero" />
    </section>
  );
};

export default Hero;

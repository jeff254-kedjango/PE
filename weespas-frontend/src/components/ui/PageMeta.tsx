import React, { useEffect } from 'react';

interface PageMetaProps {
  title: string;
  description?: string;
  ogTitle?: string;
  ogDescription?: string;
  ogType?: string;
}

const SITE_NAME = 'Weespas';

function setMetaTag(name: string, content: string, attr: 'name' | 'property' = 'name') {
  let el = document.querySelector(`meta[${attr}="${name}"]`) as HTMLMetaElement | null;
  if (!el) {
    el = document.createElement('meta');
    el.setAttribute(attr, name);
    document.head.appendChild(el);
  }
  el.content = content;
}

const PageMeta: React.FC<PageMetaProps> = ({
  title,
  description = 'Find verified spaces, trade with local shops, and move seamlessly across Kenya with Weespas — your spatial platform for listings, commerce, and mobility.',
  ogTitle,
  ogDescription,
  ogType = 'website',
}) => {
  useEffect(() => {
    const fullTitle = `${title} | ${SITE_NAME}`;
    document.title = fullTitle;

    setMetaTag('description', description);
    setMetaTag('og:title', ogTitle ?? fullTitle, 'property');
    setMetaTag('og:description', ogDescription ?? description, 'property');
    setMetaTag('og:type', ogType, 'property');
    setMetaTag('og:site_name', SITE_NAME, 'property');

    return () => {
      document.title = SITE_NAME;
    };
  }, [title, description, ogTitle, ogDescription, ogType]);

  return null;
};

export default PageMeta;

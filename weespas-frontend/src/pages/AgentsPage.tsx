import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import Icon from '../components/ui/Icon';
import Pagination from '../components/ui/Pagination';
import PageMeta from '../components/ui/PageMeta';
import { usePublicAgents } from '../hooks/usePublicAgents';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { useAuth } from '../context/AuthContext';
import VerticalVideoFeed from '../components/shorts/VerticalVideoFeed';
import type { PublicAgent } from '../types/propertyApi';
import { resolveMediaUrl } from '../utils/media';
import { formatCompactCount } from '../utils/format';
import './AgentsPage.css';

const PAGE_SIZE = 12;
const SKELETON_ITEMS = Array.from({ length: 4 });

function whatsappUrl(phone: string, text: string) {
  return `https://wa.me/${phone.replace(/\D/g, '')}?text=${encodeURIComponent(text)}`;
}

const AgentCard = React.memo<{ agent: PublicAgent }>(({ agent }) => {
  const initial = (agent.agent_name ?? 'A')[0].toUpperCase();
  const waUrl = whatsappUrl(
    agent.agent_phone_number,
    'Hi, I found you on Weespas and would like to connect.',
  );

  return (
    <div className="cc-agent-card">
      <div className="cc-agent-card__left">
        <div className="cc-agent-card__avatar">
          {agent.agent_profile_picture ? (
            <img
              // Backend returns root-relative `/uploads/avatars/...` — route
              // through resolveMediaUrl so the request hits the backend origin
              // where the StaticFiles mount serves it (same pattern as
              // AgentProfilePage.tsx's avatar render).
              src={resolveMediaUrl(agent.agent_profile_picture)}
              alt={agent.agent_name}
              loading="lazy"
              decoding="async"
            />
          ) : (
            <span className="cc-agent-card__initial">{initial}</span>
          )}
          {agent.is_verified && (
            <span className="cc-agent-card__verified" title="Verified agent">
              <Icon name="verified" size={16} />
            </span>
          )}
        </div>

        <div className="cc-agent-card__info">
          <h3 className="cc-agent-card__name">{agent.agent_name}</h3>
          {agent.email && <span className="cc-agent-card__email">{agent.email}</span>}
        </div>
      </div>

      {/* Labels wrapped in .cc-agent-btn__label so the small-viewport CSS can
          visually hide them (icon-only mode) while keeping them readable to
          screen readers via the `clip-path` technique. Native `title=` gives
          sighted users a browser tooltip with zero JS cost. */}
      <div className="cc-agent-card__actions">
        <a
          href={`tel:${agent.agent_phone_number}`}
          className="cc-agent-btn cc-agent-btn--call"
          aria-label={`Call ${agent.agent_name}`}
          title={`Call ${agent.agent_name}`}
        >
          <Icon name="phone" size={15} />
          <span className="cc-agent-btn__label">Call</span>
        </a>
        <a
          href={waUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="cc-agent-btn cc-agent-btn--whatsapp"
          aria-label={`WhatsApp ${agent.agent_name}`}
          title={`WhatsApp ${agent.agent_name}`}
        >
          <Icon name="whatsapp" size={15} />
          <span className="cc-agent-btn__label">WhatsApp</span>
        </a>
        <button
          type="button"
          disabled
          className="cc-agent-btn cc-agent-btn--chat"
          aria-label="Chat (coming soon)"
          title="Chat (coming soon)"
        >
          <Icon name="chat" size={15} />
          <span className="cc-agent-btn__label">Chat</span>
          <span className="cc-agent-btn__badge">Soon</span>
        </button>
        <button
          type="button"
          disabled
          className="cc-agent-btn cc-agent-btn--video"
          aria-label="Video call (coming soon)"
          title="Video call (coming soon)"
        >
          <Icon name="videoCall" size={15} />
          <span className="cc-agent-btn__label">Video</span>
          <span className="cc-agent-btn__badge">Soon</span>
        </button>
        <Link
          to={`/agents/${agent.id}`}
          className="cc-agent-btn cc-agent-btn--listings"
          aria-label={`View ${agent.agent_name}'s listings${agent.property_count > 0 ? ` (${agent.property_count})` : ''}`}
          title={`View listings${agent.property_count > 0 ? ` (${agent.property_count})` : ''}`}
        >
          <Icon name="eye" size={15} />
          {/* --listings-label stays visible in icon-only mode (mobile/tablet);
              the other four buttons' labels are clip-hidden there. */}
          <span className="cc-agent-btn__label cc-agent-btn__label--listings">
            Listings{agent.property_count > 0 ? ` (${formatCompactCount(agent.property_count)})` : ''}
          </span>
        </Link>
      </div>
    </div>
  );
});
AgentCard.displayName = 'AgentCard';

const SkeletonAgentCard = React.memo(() => (
  <div className="cc-agent-card cc-agent-card--skeleton" aria-hidden="true">
    <div className="cc-agent-card__left">
      <div className="cc-agent-card__avatar cc-skeleton-pulse" />
      <div className="cc-agent-card__info">
        <div className="cc-skeleton-line cc-skeleton-line--name" />
        <div className="cc-skeleton-line cc-skeleton-line--email" />
      </div>
    </div>
    <div className="cc-agent-card__actions">
      <div className="cc-skeleton-btn" />
      <div className="cc-skeleton-btn" />
      <div className="cc-skeleton-btn" />
    </div>
  </div>
));
SkeletonAgentCard.displayName = 'SkeletonAgentCard';

interface AgentsPageProps {
  /** Opens the property details modal (owned by AppContent) when a short in the
      embedded video rail fires "View details". Absent on mobile / standalone. */
  onOpenProperty?: (id: string) => void;
}

const AgentsPage: React.FC<AgentsPageProps> = ({ onOpenProperty }) => {
  const { token } = useAuth();
  // The two-column video rail is desktop/tablet only. Conditional MOUNT (not
  // display:none) keeps mobile DOM identical and avoids the shorts fetch +
  // video decode + IntersectionObserver cost on phones.
  const isDesktop = useMediaQuery('(min-width: 768px)');
  const [searchInput, setSearchInput] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [currentPage, setCurrentPage] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchInput.trim());
      setCurrentPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const { data, isLoading, isError } = usePublicAgents({
    skip: currentPage * PAGE_SIZE,
    limit: PAGE_SIZE,
    q: debouncedQuery || undefined,
  });

  const agents = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = useMemo(() => Math.ceil(total / PAGE_SIZE), [total]);

  const handlePageChange = useCallback((page: number) => {
    setCurrentPage(page);
    listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => setSearchInput(e.target.value),
    [],
  );

  // Hero + search are shared between the desktop (right column) and mobile
  // (single column) branches so each is defined exactly once. On desktop they
  // live in the right half beside the video rail; on mobile they stack on top.
  const heroBlock = (
    <div className="ag-hero">
      <div className="ag-hero__icon">
        <Icon name="user" size={44} />
      </div>
      <h1 className="ag-hero__title">Our Agents</h1>
      <p className="ag-hero__subtitle">
        Connect with our verified property experts across Kenya.
      </p>
    </div>
  );

  const searchBlock = (
    <div className="cc-agents__search ag-search">
      <Icon name="search" size={18} className="cc-agents__search-icon" />
      <input
        type="text"
        className="cc-agents__search-input"
        placeholder="Search agents by name..."
        value={searchInput}
        onChange={handleSearchChange}
      />
    </div>
  );

  // Shared between the desktop (2-col + video rail) and mobile (single-column)
  // branches so the tile list, states and pagination are defined exactly once.
  const resultsContent = (
    <>
      {isLoading && (
        <div className="cc-agents__list">
          {SKELETON_ITEMS.map((_, i) => <SkeletonAgentCard key={i} />)}
        </div>
      )}

      {isError && (
        <div className="cc-agents__empty">
          <p>Unable to load agents. Please try again later.</p>
        </div>
      )}

      {!isLoading && !isError && agents.length === 0 && (
        <div className="cc-agents__empty">
          <Icon name="search" size={32} />
          <p>No agents found{debouncedQuery ? ` for "${debouncedQuery}"` : ''}.</p>
        </div>
      )}

      {!isLoading && !isError && agents.length > 0 && (
        <div className="cc-agents__list">
          {agents.map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      )}

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        onPageChange={handlePageChange}
      />
    </>
  );

  return (
    <div className="ag-page">
      <PageMeta
        title="Our Agents"
        description="Browse Weespas's verified property agents. Connect with experts who can help you find your next home or investment."
      />

      {isDesktop ? (
        /* Desktop/tablet (≥768px): two equal halves between the navbar bottom
         * edge and the footer top border. LEFT = the vertical video feed filling
         * 100% of that height on a black backdrop; RIGHT = hero + search + the
         * scrollable agent results. */
        <div className="ag-layout">
          <div className="ag-video-col">
            <VerticalVideoFeed
              embedded
              token={token}
              onSelect={(id) => onOpenProperty?.(id)}
            />
          </div>
          <div className="ag-right-col">
            {heroBlock}
            <div className="ag-content">
              {searchBlock}
              <div ref={listRef} className="ag-results">
                {resultsContent}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <>
          {heroBlock}
          <div className="ag-content">
            {searchBlock}
            <div ref={listRef} className="ag-results">
              {resultsContent}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default AgentsPage;

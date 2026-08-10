import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import ProScaleModal from './ProScaleModal';
import type { User } from '../../types/auth';
import type { PolicySignals } from '../../api/policy';

vi.mock('../../api/contact', () => ({ submitContactForm: vi.fn() }));
import { submitContactForm } from '../../api/contact';
const mockSubmit = vi.mocked(submitContactForm);

afterEach(() => { vi.restoreAllMocks(); });

const USER = { id: 'u1', name: 'Asha', email: 'asha@kcbgroup.com', phone: '0700000000' } as User;
const SIGNALS: PolicySignals = {
  volume: 120, breadth: 5, export_count: 3, automation: 0.4, corporate_domain: true,
};

describe('ProScaleModal', () => {
  it('renders the transparency reasons from the signals', () => {
    render(<ProScaleModal user={USER} signals={SIGNALS} onClose={() => {}} />);
    expect(screen.getByText(/5 areas swept/)).toBeInTheDocument();
    expect(screen.getByText(/120 buildings looked up/)).toBeInTheDocument();
    expect(screen.getByText(/3 data exports/)).toBeInTheDocument();
    expect(screen.getByText(/corporate email domain/)).toBeInTheDocument();
  });

  it('"See business plans" files a business_plan inquiry and shows the sent state', async () => {
    mockSubmit.mockResolvedValue({} as never);
    render(<ProScaleModal user={USER} signals={SIGNALS} onClose={() => {}} />);

    fireEvent.click(screen.getByText('See business plans'));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    const payload = mockSubmit.mock.calls[0][0];
    expect(payload.inquiry_purpose).toBe('business_plan');
    expect(payload.email).toBe('asha@kcbgroup.com');     // prefilled from the user
    expect(payload.message).toContain('5 areas swept');  // reasons travel with the lead

    await waitFor(() => expect(screen.getByText(/we&rsquo;ll reach out|we’ll reach out/)).toBeInTheDocument());
  });

  it('shows a retry affordance if the lead fails to send', async () => {
    mockSubmit.mockRejectedValue(new Error('network'));
    render(<ProScaleModal user={USER} signals={SIGNALS} onClose={() => {}} />);
    fireEvent.click(screen.getByText('See business plans'));
    await waitFor(() => expect(screen.getByText('Try again')).toBeInTheDocument());
  });

  it('"Not now" dismisses without sending anything', () => {
    const onClose = vi.fn();
    render(<ProScaleModal user={USER} signals={SIGNALS} onClose={onClose} />);
    fireEvent.click(screen.getByText('Not now'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mockSubmit).not.toHaveBeenCalled();
  });
});

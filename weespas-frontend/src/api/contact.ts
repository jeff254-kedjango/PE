import { API_BASE_URL, fetchJson } from './config';

export interface ContactFormData {
  inquiry_purpose: string;
  description: string;
  full_name?: string;
  email?: string;
  organization?: string;
  phone?: string;
  message: string;
}

export interface ContactResponse {
  id: string;
  inquiry_purpose: string;
  description: string;
  full_name: string | null;
  email: string | null;
  organization: string | null;
  phone: string | null;
  message: string;
  is_read: boolean;
  created_at: string;
}

export async function submitContactForm(data: ContactFormData): Promise<ContactResponse> {
  return fetchJson<ContactResponse>(`${API_BASE_URL}/contact`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

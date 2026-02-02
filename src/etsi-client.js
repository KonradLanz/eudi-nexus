import fetch from 'node-fetch';
import { CookieJar } from 'tough-cookie';
import fetchCookie from 'fetch-cookie';

const BASE_URL = 'https://portal.etsi.org';
const DOCBOX_URL = 'https://docbox.etsi.org';

export class ETSIClient {
  constructor() {
    this.cookieJar = new CookieJar();
    this.fetch = fetchCookie(fetch, this.cookieJar);
    this.isLoggedIn = false;
  }

  getDefaultHeaders() {
    return {
      'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
      'accept-language': 'en-US,en;q=0.9,de;q=0.8',
      'cache-control': 'no-cache',
      'pragma': 'no-cache',
      'sec-ch-ua': '"Chromium";v="144", "Google Chrome";v="144"',
      'sec-ch-ua-mobile': '?0',
      'sec-ch-ua-platform': '"macOS"',
      'sec-fetch-dest': 'document',
      'sec-fetch-mode': 'navigate',
      'sec-fetch-site': 'same-origin',
      'sec-fetch-user': '?1',
      'upgrade-insecure-requests': '1',
      'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
    };
  }

  async login(username, password) {
    if (!username || !password) {
      throw new Error('Username and password are required');
    }

    try {
      // Step 1: Visit home page to get initial cookies
      console.log('  → Fetching initial session...');
      const homeResponse = await this.fetch(`${BASE_URL}/home.aspx`, {
        headers: this.getDefaultHeaders(),
        method: 'GET'
      });

      if (!homeResponse.ok) {
        throw new Error(`Failed to load home page: ${homeResponse.status}`);
      }

      // Step 2: Perform login
      console.log('  → Sending credentials...');
      const loginResponse = await this.fetch(`${BASE_URL}/ETSIPages/LoginEOL.ashx`, {
        headers: {
          ...this.getDefaultHeaders(),
          'accept': '*/*',
          'content-type': 'application/json; charset=UTF-8',
          'sec-fetch-dest': 'empty',
          'sec-fetch-mode': 'cors',
          'Referer': `${BASE_URL}/home.aspx`
        },
        method: 'POST',
        body: JSON.stringify({ username, password })
      });

      if (!loginResponse.ok) {
        throw new Error(`Login request failed: ${loginResponse.status}`);
      }

      const loginResult = await loginResponse.text();
      console.log('  → Login response:', loginResult.substring(0, 100));

      // Step 3: Verify login by checking success endpoint
      console.log('  → Verifying login...');
      const successResponse = await this.fetch(`${BASE_URL}/ETSIPages/success.txt`, {
        headers: {
          ...this.getDefaultHeaders(),
          'accept': '*/*',
          'sec-fetch-dest': 'empty',
          'sec-fetch-mode': 'cors',
          'Referer': `${BASE_URL}/home.aspx`
        },
        method: 'GET'
      });

      // Step 4: Re-visit home page to confirm session
      const verifyResponse = await this.fetch(`${BASE_URL}/home.aspx`, {
        headers: this.getDefaultHeaders(),
        method: 'GET'
      });

      this.isLoggedIn = verifyResponse.ok;
      return this.isLoggedIn;
    } catch (error) {
      console.error('Login error:', error.message);
      return false;
    }
  }

  async getMeetings(options = {}) {
    const defaultOptions = {
      startRow: 0,
      resultsPerPage: 100,
      sortBy: 'Date',
      sortAscending: true,
      startDate: new Date().toISOString().split('T')[0] + ' 00:00:00',
      endDate: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0] + ' 00:00:00',
      tbs: [0],
      includeChildTbs: true,
      includeNonTBMeetings: true,
      reference: '',
      registered: false
    };

    const opts = { ...defaultOptions, ...options };

    const response = await this.fetch(`${BASE_URL}/webservices/Rest/Meetings.svc/GetMeetings`, {
      headers: {
        ...this.getDefaultHeaders(),
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json;charset=UTF-8',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'Referer': `${BASE_URL}/webservices/client/Meetings/MeetingsFrame.html`
      },
      method: 'POST',
      body: JSON.stringify({
        getMeetingsInput: {
          StartRow: opts.startRow,
          ResultsPerPage: opts.resultsPerPage,
          SortBy: opts.sortBy,
          SortAscending: opts.sortAscending,
          StartDate: opts.startDate,
          EndDate: opts.endDate,
          Tbs: opts.tbs,
          IncludeChildTbs: opts.includeChildTbs,
          IncludeNonTBMeetings: opts.includeNonTBMeetings,
          Reference: opts.reference,
          Registered: opts.registered
        }
      })
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch meetings: ${response.status}`);
    }

    const data = await response.json();
    return data.GetMeetingsResult?.Meetings || data.d?.Meetings || [];
  }

  async fetchWithAuth(url, options = {}) {
    const defaultHeaders = this.getDefaultHeaders();
    
    return this.fetch(url, {
      ...options,
      headers: {
        ...defaultHeaders,
        ...options.headers
      }
    });
  }

  async getDocboxContent(path = '/ESI/ESI') {
    const url = `${DOCBOX_URL}${path}`;
    console.log(`  → Fetching: ${url}`);
    
    // First, ensure we have docbox cookies by visiting the base
    const portalCookies = this.getCookies();
    
    const response = await this.fetch(url, {
      headers: {
        ...this.getDefaultHeaders(),
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': `${BASE_URL}/home.aspx`
      },
      redirect: 'follow'
    });

    if (!response.ok) {
      // Try to get more info about the error
      const text = await response.text().catch(() => '');
      throw new Error(`Failed to access docbox: ${response.status} ${response.statusText} - ${text.substring(0, 200)}`);
    }

    return response;
  }

  async downloadDocboxFile(filePath) {
    const url = `${DOCBOX_URL}${filePath}`;
    
    const response = await this.fetch(url, {
      headers: {
        ...this.getDefaultHeaders(),
        'accept': '*/*',
        'Referer': `${DOCBOX_URL}${filePath.substring(0, filePath.lastIndexOf('/'))}`
      },
      redirect: 'follow'
    });

    return response;
  }

  getCookies() {
    return this.cookieJar.getCookieStringSync(BASE_URL);
  }

  getDocboxCookies() {
    return this.cookieJar.getCookieStringSync(DOCBOX_URL);
  }
}

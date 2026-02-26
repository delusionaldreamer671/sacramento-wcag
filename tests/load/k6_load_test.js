import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.TARGET_URL || 'http://localhost:8000';

// Safety check - don't accidentally hit production
if (BASE_URL.includes('run.app') && !__ENV.ALLOW_PRODUCTION) {
    throw new Error('Refusing to load test production. Set ALLOW_PRODUCTION=true to override.');
}

export const options = {
    scenarios: {
        smoke: {
            executor: 'constant-vus',
            vus: 1,
            duration: '30s',
            exec: 'smokeTest',
        },
    },
    thresholds: {
        'http_req_duration{scenario:smoke}': ['p(95)<500'],
        'http_req_failed{scenario:smoke}': ['rate<0.01'],
    },
};

export function smokeTest() {
    const healthRes = http.get(`${BASE_URL}/api/health`);
    check(healthRes, {
        'health status is 200': (r) => r.status === 200,
        'health returns healthy': (r) => JSON.parse(r.body).status === 'healthy',
    });

    const rulesRes = http.get(`${BASE_URL}/api/wcag-rules`);
    check(rulesRes, {
        'rules status is 200': (r) => r.status === 200,
        'returns 50 rules': (r) => JSON.parse(r.body).length === 50,
    });

    sleep(1);
}

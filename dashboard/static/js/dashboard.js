// SGLang CI Dashboard - JavaScript Utilities

/**
 * Format date from YYYYMMDD to YYYY-MM-DD
 */
function formatDateDisplay(dateStr) {
    if (dateStr.length === 8) {
        return `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}`;
    }
    return dateStr;
}

/**
 * Format date from YYYY-MM-DD to YYYYMMDD
 */
function formatDateApi(dateStr) {
    return dateStr.replace(/-/g, '');
}

/**
 * Get status badge HTML
 */
function getStatusBadge(status) {
    const badges = {
        'passed': '<span class="badge bg-success"><i class="bi bi-check-circle"></i> All Passed</span>',
        'failed': '<span class="badge bg-danger"><i class="bi bi-x-circle"></i> Failed</span>',
        'partial': '<span class="badge bg-warning"><i class="bi bi-dash-circle"></i> Partial</span>',
        'unknown': '<span class="badge bg-secondary"><i class="bi bi-question-circle"></i> Unknown</span>'
    };
    return badges[status] || badges['unknown'];
}

/**
 * Get status icon HTML
 */
function getStatusIcon(status, exists = true) {
    // Handle "not run" status explicitly
    if (!exists || status === 'not run' || status === 'not_run') {
        return '<i class="bi bi-skip-forward-fill text-primary" style="font-weight: 600;"></i>';
    }

    const icons = {
        'pass': '<i class="bi bi-check-circle-fill text-success"></i>',
        'fail': '<i class="bi bi-x-circle-fill text-danger"></i>',
        'unknown': '<i class="bi bi-question-circle-fill text-warning"></i>'
    };
    return icons[status] || icons['unknown'];
}

/**
 * Get status CSS class for list items
 */
function getStatusClass(status, exists = true) {
    // Handle "not run" status explicitly
    if (!exists || status === 'not run' || status === 'not_run') {
        return 'list-group-item-info';
    }

    const classes = {
        'pass': 'list-group-item-success',
        'fail': 'list-group-item-danger',
        'unknown': 'list-group-item-warning'
    };
    return classes[status] || '';
}

/**
 * Format runtime string (e.g., "5h 30m" or "45m")
 */
function formatRuntime(runtimeStr) {
    if (!runtimeStr) return '';

    // Parse "Xh Ym" or "Ym" format
    let hours = 0;
    let minutes = 0;

    if (runtimeStr.includes('h')) {
        const parts = runtimeStr.split('h');
        hours = parseInt(parts[0].trim());
        if (parts.length > 1 && parts[1].includes('m')) {
            minutes = parseInt(parts[1].replace('m', '').trim());
        }
    } else if (runtimeStr.includes('m')) {
        minutes = parseInt(runtimeStr.replace('m', '').trim());
    }

    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    }
    return `${minutes}m`;
}

/**
 * Show loading spinner
 */
function showLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.style.display = 'block';
    }
}

/**
 * Hide loading spinner
 */
function hideLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.style.display = 'none';
    }
}

/**
 * Show element
 */
function showElement(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.style.display = 'block';
    }
}

/**
 * Hide element
 */
function hideElement(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.style.display = 'none';
    }
}

/**
 * Display error message
 */
function displayError(elementId, message) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> ${message}
            </div>
        `;
    }
}

/**
 * Display warning message
 */
function displayWarning(elementId, message) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = `
            <div class="alert alert-warning">
                <i class="bi bi-exclamation-triangle"></i> ${message}
            </div>
        `;
    }
}

/**
 * Display info message
 */
function displayInfo(elementId, message) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = `
            <div class="alert alert-info">
                <i class="bi bi-info-circle"></i> ${message}
            </div>
        `;
    }
}

/**
 * Fetch JSON with error handling
 */
async function fetchJSON(url) {
    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Fetch error:', error);
        throw error;
    }
}

/**
 * Debounce function to limit rapid function calls
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Copy text to clipboard
 */
function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            console.log('Copied to clipboard');
        }).catch(err => {
            console.error('Failed to copy:', err);
        });
    } else {
        // Fallback for older browsers
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            console.log('Copied to clipboard (fallback)');
        } catch (err) {
            console.error('Failed to copy:', err);
        }
        document.body.removeChild(textarea);
    }
}

/**
 * Get current date in Pacific Time (YYYY-MM-DD format)
 */
function getCurrentDate() {
    // Get current time in Pacific Time (UTC-8 for PST, UTC-7 for PDT)
    const now = new Date();

    // Convert to Pacific Time using Intl API
    const pacificDate = new Date(now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles' }));

    const year = pacificDate.getFullYear();
    const month = String(pacificDate.getMonth() + 1).padStart(2, '0');
    const day = String(pacificDate.getDate()).padStart(2, '0');

    return `${year}-${month}-${day}`;
}

/**
 * Get date N days ago in YYYY-MM-DD format
 */
function getDaysAgo(days) {
    const date = new Date();
    date.setDate(date.getDate() - days);
    return date.toISOString().split('T')[0];
}

/**
 * Parse date string to Date object
 */
function parseDate(dateStr) {
    if (dateStr.length === 8) {
        // YYYYMMDD format
        const year = dateStr.slice(0, 4);
        const month = dateStr.slice(4, 6);
        const day = dateStr.slice(6, 8);
        return new Date(`${year}-${month}-${day}`);
    }
    return new Date(dateStr);
}

/**
 * Format number with commas
 */
function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * Calculate percentage
 */
function calculatePercentage(value, total) {
    if (total === 0) return 0;
    const result = (value / total) * 100;
    try {
        return (result || 0).toFixed(1);
    } catch (e) {
        console.error('Error calculating percentage:', e);
        return 0;
    }
}

/**
 * Normalize date string to YYYY-MM-DD format.
 */
function normalizeDashboardDate(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') {
        return null;
    }

    const trimmed = dateStr.trim();

    if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
        return trimmed;
    }

    if (/^\d{8}$/.test(trimmed)) {
        return `${trimmed.slice(0, 4)}-${trimmed.slice(4, 6)}-${trimmed.slice(6, 8)}`;
    }

    return null;
}

/**
 * Convert Date object to YYYY-MM-DD string without mutating the original date.
 */
function dateObjectToYMD(dateObj) {
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const day = String(dateObj.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

/**
 * Prepare available dates by normalizing, de-duplicating, and removing future dates.
 */
function computeAvailableDashboardDates(rawDates = []) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const uniqueDates = new Set();

    rawDates.forEach((raw) => {
        const normalized = normalizeDashboardDate(raw);
        if (!normalized) {
            return;
        }

        const dateObj = new Date(normalized);
        dateObj.setHours(0, 0, 0, 0);

        if (dateObj > today) {
            return;
        }

        uniqueDates.add(normalized);
    });

    const sortedDates = Array.from(uniqueDates);
    sortedDates.sort();
    sortedDates.reverse(); // newest first

    return {
        sortedDates,
        dateSet: new Set(sortedDates),
    };
}

/**
 * Initialize a standard dashboard date picker with consistent behaviour.
 */
function initializeDashboardDatePicker({
    selector,
    rawDates = [],
    defaultDate = null,
    onDateChange = null,
    calendarClass = 'ci-dashboard-calendar',
} = {}) {
    const { sortedDates, dateSet } = computeAvailableDashboardDates(rawDates);

    let resolvedDefaultDate = normalizeDashboardDate(defaultDate);
    if (!resolvedDefaultDate) {
        resolvedDefaultDate =
            sortedDates.length > 0 ? sortedDates[0] : getCurrentDate();
    }

    if (resolvedDefaultDate && !dateSet.has(resolvedDefaultDate)) {
        dateSet.add(resolvedDefaultDate);
        sortedDates.unshift(resolvedDefaultDate);
    }

    // Fallback if flatpickr is unavailable
    if (typeof flatpickr === 'undefined') {
        console.warn('flatpickr is not available; falling back to native date input.');
        const input = document.querySelector(selector);
        if (input) {
            input.value = resolvedDefaultDate;
            input.addEventListener('change', (event) => {
                if (typeof onDateChange === 'function') {
                    onDateChange(event.target.value);
                }
            });
        }

        return {
            instance: null,
            defaultDate: resolvedDefaultDate,
            availableDates: sortedDates,
        };
    }

    const instance = flatpickr(selector, {
        defaultDate: resolvedDefaultDate,
        dateFormat: 'Y-m-d',
        disable: [
            (date) => {
                const dateCopy = new Date(date.getTime());
                dateCopy.setHours(0, 0, 0, 0);

                const today = new Date();
                today.setHours(0, 0, 0, 0);

                const isFuture = dateCopy > today;
                const dateStr = dateObjectToYMD(dateCopy);
                const notAvailable = !dateSet.has(dateStr);

                return isFuture || notAvailable;
            },
        ],
        onChange: (selectedDates, dateStr) => {
            if (typeof onDateChange === 'function') {
                onDateChange(dateStr);
            }
        },
        onReady: (selectedDates, dateStr, fpInstance) => {
            if (calendarClass && fpInstance && fpInstance.calendarContainer) {
                fpInstance.calendarContainer.classList.add(calendarClass);
            }
        },
    });

    return {
        instance,
        defaultDate: resolvedDefaultDate,
        availableDates: sortedDates,
    };
}

/**
 * Add fade-in animation to element
 */
function fadeIn(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.classList.add('fade-in');
    }
}

/**
 * Initialize Bootstrap tooltips
 */
function initTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

/**
 * Initialize Bootstrap popovers
 */
function initPopovers() {
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Bootstrap components if available
    if (typeof bootstrap !== 'undefined') {
        initTooltips();
        initPopovers();
    }

    // Add current year to footer if element exists
    const yearElement = document.querySelector('.current-year');
    if (yearElement) {
        yearElement.textContent = new Date().getFullYear();
    }
});

// Export functions for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        formatDateDisplay,
        formatDateApi,
        getStatusBadge,
        getStatusIcon,
        getStatusClass,
        formatRuntime,
        showLoading,
        hideLoading,
        showElement,
        hideElement,
        displayError,
        displayWarning,
        displayInfo,
        fetchJSON,
        debounce,
        copyToClipboard,
        getCurrentDate,
        getDaysAgo,
        parseDate,
        formatNumber,
        calculatePercentage,
        fadeIn,
        normalizeDashboardDate,
        initializeDashboardDatePicker,
        computeAvailableDashboardDates,
        dateObjectToYMD,
    };
}

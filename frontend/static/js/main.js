// ============================================
// ChargeEase 2.0 - Main JavaScript
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    
    // 1. Page Load Animation
    document.body.classList.add('page-loaded');
    
    // 2. Navbar Scroll Effect
    const navbar = document.querySelector('.navbar');
    window.addEventListener('scroll', () => {
        if (window.scrollY > 50) {
            navbar.style.boxShadow = '0 4px 20px rgba(0, 0, 0, 0.3)';
        } else {
            navbar.style.boxShadow = 'none';
        }
    });
    
    // 3. Tilt Effect on Cards
    const cards = document.querySelectorAll('.station-card, .action-card, .card');
    cards.forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const px = (x / rect.width) - 0.5;
            const py = (y / rect.height) - 0.5;
            
            card.style.transform = `perspective(1000px) rotateX(${py * -5}deg) rotateY(${px * 5}deg) translateY(-4px)`;
        });
        
        card.addEventListener('mouseleave', () => {
            card.style.transform = '';
        });
    });
    
    // 4. Toast Notifications
    const flashMessages = document.querySelectorAll('.flash');
    flashMessages.forEach(msg => {
        setTimeout(() => {
            msg.style.opacity = '0';
            msg.style.transform = 'translateY(-10px)';
            setTimeout(() => msg.remove(), 300);
        }, 4000);
    });
    
    // 5. Animated Counters
    const counters = document.querySelectorAll('.stat-value, .hero-stat-strip strong');
    counters.forEach(counter => {
        const target = parseInt(counter.textContent);
        if (!isNaN(target)) {
            const duration = 1000;
            const step = target / (duration / 16);
            let current = 0;
            
            const animate = () => {
                current += step;
                if (current < target) {
                    counter.textContent = Math.floor(current);
                    requestAnimationFrame(animate);
                } else {
                    counter.textContent = target;
                }
            };
            
            animate();
        }
    });
    
    // 6. Smooth Scroll
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({ behavior: 'smooth' });
            }
        });
    });
    
    // 7. Navbar Mobile Toggle
    const navToggle = document.querySelector('.nav-toggle');
    const mainNav = document.querySelector('.main-nav');
    
    if (navToggle && mainNav) {
        navToggle.addEventListener('click', () => {
            mainNav.classList.toggle('open');
            navToggle.classList.toggle('active');
        });
    }
    
    // 8. Notification Bell
    const bell = document.getElementById('notifBell');
    const dropdown = document.getElementById('notifDropdown');
    
    if (bell && dropdown) {
        bell.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.classList.toggle('open');
        });
        
        document.addEventListener('click', (e) => {
            if (!dropdown.contains(e.target) && e.target !== bell) {
                dropdown.classList.remove('open');
            }
        });
    }
    
    // 9. Form Input Focus Effect
    const inputs = document.querySelectorAll('.input-wrap input, .input-wrap select');
    inputs.forEach(input => {
        input.addEventListener('focus', () => {
            input.closest('.input-wrap').style.borderColor = 'var(--primary)';
            input.closest('.input-wrap').style.boxShadow = '0 0 0 3px rgba(0, 212, 255, 0.1)';
        });
        
        input.addEventListener('blur', () => {
            input.closest('.input-wrap').style.borderColor = 'var(--line)';
            input.closest('.input-wrap').style.boxShadow = 'none';
        });
    });
    
    // 10. Copy to Clipboard (for API keys, etc.)
    document.querySelectorAll('[data-copy]').forEach(btn => {
        btn.addEventListener('click', () => {
            const text = btn.getAttribute('data-copy');
            navigator.clipboard.writeText(text).then(() => {
                btn.textContent = '✓ Copied!';
                setTimeout(() => {
                    btn.textContent = 'Copy';
                }, 2000);
            });
        });
    });
});
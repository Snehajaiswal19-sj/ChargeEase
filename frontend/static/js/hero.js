// ============================================
// ChargeEase - Cinematic Hero (No 3D Shape)
// ============================================

document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const heroBg = document.querySelector('.hero-bg img');
  const progressBar = document.querySelector('.scroll-progress');
  const sections = document.querySelectorAll('.hero-section');
  const loadingOverlay = document.querySelector('.loading-overlay');
  
  // Hide loading after 1s
  setTimeout(() => {
    if (loadingOverlay) loadingOverlay.classList.add('loaded');
  }, 1000);
  
  // Scroll Parallax + Progress
  let scrollY = 0;
  window.addEventListener('scroll', () => {
    scrollY = window.scrollY;
    
    // Parallax background (slow move)
    if (heroBg) {
      heroBg.style.transform = `translateY(${scrollY * 0.2}px) scale(1.1)`;
    }
    
    // Progress bar
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const progress = scrollY / docHeight;
    if (progressBar) {
      progressBar.style.transform = `scaleX(${progress})`;
    }
  });
  
  // Intersection Observer for sections
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.querySelector('.section-content').classList.add('visible');
      }
    });
  }, { threshold: 0.4 });
  
  sections.forEach(section => observer.observe(section));
  
  // Active nav link
  const navLinks = document.querySelectorAll('.hero-nav-menu a');
  const sectionObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id;
        navLinks.forEach(link => {
          link.classList.remove('active');
          if (link.getAttribute('href') === `#${id}`) {
            link.classList.add('active');
          }
        });
      }
    });
  }, { threshold: 0.5 });
  
  sections.forEach(section => sectionObserver.observe(section));
  
  // Smooth scroll for nav links
  navLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.querySelector(link.getAttribute('href'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });
});
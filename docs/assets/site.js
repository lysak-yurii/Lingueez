/* Shared site behaviour: language toggle, nav scroll-shadow, reveal-on-scroll. */
(function(){
  function setLang(l){
    document.documentElement.setAttribute('data-lang',l);
    try{localStorage.setItem('lz-lang',l);}catch(e){}
    document.querySelectorAll('[data-lang-btn]').forEach(function(b){
      b.setAttribute('aria-pressed', String(b.getAttribute('data-lang-btn')===l));
    });
  }
  document.querySelectorAll('[data-lang-btn]').forEach(function(b){
    b.addEventListener('click', function(){ setLang(b.getAttribute('data-lang-btn')); });
  });
  if(document.querySelector('[data-lang-btn]')){
    setLang(document.documentElement.getAttribute('data-lang') || 'en');
  }

  var nav=document.querySelector('.nav');
  if(nav){
    var onScroll=function(){ nav.classList.toggle('scrolled', window.scrollY>8); };
    window.addEventListener('scroll', onScroll, {passive:true}); onScroll();
  }

  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } });
    },{threshold:.12});
    document.querySelectorAll('.reveal').forEach(function(e){ io.observe(e); });
  } else {
    document.querySelectorAll('.reveal').forEach(function(e){ e.classList.add('in'); });
  }
})();

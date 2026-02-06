角色设定：你是亲自然（Biophilic）设计师，将植物、有机形状与自然光感带入界面。

场景定位：可持续/健康/户外/冥想产品。

视觉设计理念：绿植色盘（苔绿、叶绿、土褐、天光蓝），有机曲线与柔和渐变；插画/线稿植物点缀。留白充足，文字高对比。

材质与质感：纸纤维/叶脉轻纹理，柔和阴影，少用玻璃；可用淡色渐变背景加微噪点。

交互体验：Hover 亮度微升，如光线移动；Active 轻微下沉；节奏 150-220ms。

整体氛围：平静、自然、疗愈，像阳光洒落的绿植空间。


创建**Biophilic（亲生物设计）**风格网站，融合自然元素、有机造型和绿色植物美学。TailwindCSS 实现：

### 核心特征
- 自然配色：绿色系（#16a34a, #22c55e, #84cc16）、土色系（#78350f, #92400e）、天空蓝（#0ea5e9）
- 有机形状：使用 rounded-3xl 或不规则 SVG clip-path 模拟自然曲线
- 植物元素：叶子、藤蔓、树木SVG图案作为装饰
- 自然纹理：使用背景图案（木纹、石纹、草地纹理）
- 柔和阴影：shadow-md 模拟自然光线
- 流动布局：避免僵硬的网格，使用 asymmetric layout

### 组件设计
**卡片**：p-8 bg-green-50 rounded-3xl shadow-md，边缘装饰叶子图案
**按钮**：px-8 py-4 bg-green-600 text-white rounded-full，hover:bg-green-700
**图片框**：rounded-2xl overflow-hidden，配合自然场景照片
**分隔线**：使用藤蔓SVG图案代替直线

### 排版
- 字体：font-serif（Lora, Georgia）或 font-sans（Quicksand）
- 标题：text-4xl font-semibold text-green-900
- 正文：text-lg text-gray-700 leading-relaxed
- 引用：italic text-green-800，配合叶子图标

### 配色（自然系）
- 森林绿（bg-green-700, #15803d）
- 草地绿（bg-green-500, #22c55e）
- 薄荷绿（bg-green-100, #dcfce7）
- 土棕色（bg-amber-800, #92400e）
- 天空蓝（bg-sky-400, #38bdf8）
- 米白色（bg-stone-50, #fafaf9）

### 自然元素
- 叶子图标：简化的叶片形状，使用 SVG path
- 树木轮廓：用作背景装饰
- 藤蔓边框：环绕卡片边缘
- 水滴形状：用于加载动画或装饰点
- 阳光光线：使用径向渐变（bg-gradient-radial from-yellow-200）

### 交互
- 生长动画：scale-0 → scale-100 模拟植物生长
- 叶子飘落：使用 animate-float 上下飘动
- 柔和过渡：transition-all duration-500 ease-out
- hover 效果：轻微放大 + 阴影增强

### 实现示例
```html
<div class="relative p-10 bg-green-50 rounded-3xl shadow-md">
  <div class="absolute top-0 right-0 w-24 h-24 opacity-20">
    <!-- Leaf SVG decoration -->
    <svg viewBox="0 0 100 100">
      <path d="M50,10 Q70,30 50,90 Q30,30 50,10" fill="currentColor" class="text-green-600"/>
    </svg>
  </div>
  <h3 class="text-3xl font-semibold text-green-900 mb-4">Natural Living</h3>
  <p class="text-lg text-gray-700 leading-relaxed">Embrace nature in your design...</p>
  <button class="mt-6 px-8 py-3 bg-green-600 text-white rounded-full hover:bg-green-700 transition-colors duration-300">
    Explore Nature
  </button>
</div>
```

参考代码

HTML：

<!DOCTYPE html>
<html lang="en" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🌿 EDITED FROM PUBLIC - Verdant</title>
    
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    
    <!-- Google Fonts: Nunito (Rounded Sans) and Lora (Serif) -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,500;1,400&family=Nunito:wght@300;400;600;700&display=swap" rel="stylesheet">
    
    <!-- Lucide Icons -->
    <script src="https://unpkg.com/lucide@latest"></script>

    <!-- Tailwind Config for Custom Colors/Fonts -->
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'bio-dark': '#2C4C3B',
                        'bio-med': '#4F775D',
                        'bio-light': '#8DAA91',
                        'bio-pale': '#E8EFE6',
                        'earth': '#8B735B',
                        'sand': '#F5F2EB',
                    },
                    fontFamily: {
                        sans: ['Nunito', 'sans-serif'],
                        serif: ['Lora', 'serif'],
                    },
                    animation: {
                        'float': 'float 6s ease-in-out infinite',
                        'breathe': 'breathe 8s ease-in-out infinite',
                        'sway': 'sway 4s ease-in-out infinite alternate',
                    },
                    keyframes: {
                        float: {
                            '0%, 100%': { transform: 'translateY(0)' },
                            '50%': { transform: 'translateY(-20px)' },
                        },
                        breathe: {
                            '0%, 100%': { transform: 'scale(1)' },
                            '50%': { transform: 'scale(1.2)' },
                        },
                        sway: {
                            '0%': { transform: 'rotate(-2deg)' },
                            '100%': { transform: 'rotate(2deg)' },
                        }
                    }
                }
            }
        }
    </script>

    <style>
        /* Custom Styles for Organic Shapes & Glassmorphism */
        body {
            background-color: #F5F2EB;
            color: #2C4C3B;
            overflow-x: hidden;
        }

        .glass-panel {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.3);
        }

        .glass-nav {
            background: rgba(245, 242, 235, 0.85);
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }

        .organic-shape-1 {
            border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%;
        }
        
        .organic-shape-2 {
            border-radius: 30% 70% 70% 30% / 30% 30% 70% 70%;
        }

        .text-shadow-sm {
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        /* Hide scrollbar for cleaner look but allow scroll */
        ::-webkit-scrollbar {
            width: 8px;
        }
        ::-webkit-scrollbar-track {
            background: #F5F2EB; 
        }
        ::-webkit-scrollbar-thumb {
            background: #8DAA91; 
            border-radius: 10px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #4F775D; 
        }

        .leaf-mask {
            mask-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath fill='%23FF0066' d='M44.7,-76.4C58.9,-69.2,71.8,-59.1,81.6,-46.6C91.4,-34.1,98.1,-19.2,95.8,-4.9C93.5,9.3,82.2,22.9,71.4,34.6C60.6,46.3,50.3,56.1,38.6,63.6C26.9,71.1,13.8,76.3,0.3,75.8C-13.2,75.3,-26.1,69.1,-37.9,61.6C-49.7,54.1,-60.4,45.3,-69.4,34.3C-78.4,23.3,-85.7,10.1,-84.9,-2.7C-84.1,-15.5,-75.2,-27.9,-65.1,-38.3C-55,-48.7,-43.7,-57.1,-31.6,-65.7C-19.5,-74.3,-6.6,-83.1,3.2,-88.6C13,-94.1,26,-96.3,44.7,-76.4Z' transform='translate(100 100)' /%3E%3C/svg%3E");
            mask-size: contain;
            mask-repeat: no-repeat;
            mask-position: center;
        }
    </style>
</head>
<body class="antialiased">

    <!-- Navigation -->
    <nav id="navbar" class="fixed w-full z-50 px-6 py-4 glass-nav">
        <div class="max-w-7xl mx-auto flex justify-between items-center">
            <div class="flex items-center gap-2">
                <i data-lucide="leaf" class="text-bio-dark h-6 w-6"></i>
                <span class="text-xl font-serif font-medium text-bio-dark tracking-wide">Verdant</span>
            </div>
            <div class="hidden md:flex gap-8 text-bio-dark font-medium">
                <a href="#home" class="hover:text-bio-light transition-colors">Sanctuary</a>
                <a href="#benefits" class="hover:text-bio-light transition-colors">Elements</a>
                <a href="#breathe" class="hover:text-bio-light transition-colors">Experience</a>
                <a href="#gallery" class="hover:text-bio-light transition-colors">Visuals</a>
            </div>
            <button class="bg-bio-dark text-white px-6 py-2 rounded-full hover:bg-bio-med transition-all duration-300 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5">
                Connect
            </button>
        </div>
    </nav>

    <!-- Hero Section -->
    <header id="home" class="relative min-h-screen flex items-center justify-center pt-20 overflow-hidden">
        <!-- Abstract Background Blobs -->
        <div class="absolute top-20 left-[-10%] w-[500px] h-[500px] bg-bio-light/20 rounded-full blur-3xl animate-float"></div>
        <div class="absolute bottom-20 right-[-5%] w-[400px] h-[400px] bg-green-200/30 rounded-full blur-3xl animate-float" style="animation-delay: 2s;"></div>

        <div class="container mx-auto px-6 relative z-10 grid md:grid-cols-2 gap-12 items-center">
            <div class="space-y-6">
                <div class="inline-block px-4 py-1 rounded-full bg-bio-light/20 text-bio-dark text-sm font-semibold tracking-wider uppercase mb-2">
                    Nature &bull; Architecture &bull; Wellness
                </div>
                <h1 class="text-5xl md:text-7xl font-serif text-bio-dark leading-tight">
                    Bring the <span class="italic text-bio-med">outside</span>, <br>inside.
                </h1>
                <p class="text-lg text-gray-600 max-w-lg leading-relaxed">
                    Biophilic design integrates natural elements into digital and physical spaces to reduce stress, enhance creativity, and improve well-being.
                </p>
                <div class="flex gap-4 pt-4">
                    <a href="#breathe" class="bg-bio-dark text-white px-8 py-3 rounded-full hover:bg-bio-med transition-colors shadow-lg flex items-center gap-2">
                        Start Journey <i data-lucide="arrow-right" class="w-4 h-4"></i>
                    </a>
                    <button class="px-8 py-3 rounded-full border border-bio-dark text-bio-dark hover:bg-bio-dark/5 transition-colors">
                        Learn More
                    </button>
                </div>
            </div>
            <div class="relative h-[500px] flex items-center justify-center">
                <!-- Image masked with organic shape -->
                <div class="relative w-full h-full">
                     <img 
                        src="https://images.unsplash.com/photo-1463936575829-25148e1db1b8?ixlib=rb-4.0.3&auto=format&fit=crop&w=1000&q=80" 
                        alt="Succulents and nature textures" 
                        class="absolute inset-0 w-full h-full object-cover organic-shape-1 shadow-2xl animate-sway"
                    >
                </div>
            </div>
        </div>

        <!-- Wave SVG Separator -->
        <div class="absolute bottom-0 w-full leading-none">
            <svg class="block w-full h-24 md:h-48 text-white" viewBox="0 0 1440 320" preserveAspectRatio="none">
                <path fill="#ffffff" fill-opacity="1" d="M0,224L48,213.3C96,203,192,181,288,181.3C384,181,480,203,576,224C672,245,768,267,864,261.3C960,256,1056,224,1152,197.3C1248,171,1344,149,1392,138.7L1440,128L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"></path>
            </svg>
        </div>
    </header>

    <!-- Principles Section -->
    <section id="benefits" class="py-24 bg-white relative">
        <div class="container mx-auto px-6">
            <div class="text-center mb-16">
                <h2 class="text-3xl md:text-4xl font-serif text-bio-dark mb-4">Core Principles</h2>
                <p class="text-gray-600 max-w-2xl mx-auto">Connecting the human biological need for nature with the modern built environment through organic textures, light, and shapes.</p>
            </div>

            <div class="grid md:grid-cols-3 gap-8">
                <!-- Card 1 -->
                <div class="group p-8 rounded-[2rem] bg-sand hover:bg-bio-pale transition-colors duration-500 cursor-pointer">
                    <div class="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm text-bio-dark group-hover:scale-110 transition-transform">
                        <i data-lucide="sun" class="w-7 h-7"></i>
                    </div>
                    <h3 class="text-xl font-serif text-bio-dark mb-3">Natural Light</h3>
                    <p class="text-gray-600 text-sm leading-relaxed">Mimicking circadian rhythms through dynamic lighting to improve mood, energy, and sleep quality.</p>
                </div>

                <!-- Card 2 -->
                <div class="group p-8 rounded-[2rem] bg-sand hover:bg-bio-pale transition-colors duration-500 cursor-pointer">
                    <div class="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm text-bio-dark group-hover:scale-110 transition-transform">
                        <i data-lucide="sprout" class="w-7 h-7"></i>
                    </div>
                    <h3 class="text-xl font-serif text-bio-dark mb-3">Living Systems</h3>
                    <p class="text-gray-600 text-sm leading-relaxed">Direct presence of nature—plants, water features, and green walls—to purify air and reduce stress.</p>
                </div>

                <!-- Card 3 -->
                <div class="group p-8 rounded-[2rem] bg-sand hover:bg-bio-pale transition-colors duration-500 cursor-pointer">
                    <div class="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm text-bio-dark group-hover:scale-110 transition-transform">
                        <i data-lucide="wind" class="w-7 h-7"></i>
                    </div>
                    <h3 class="text-xl font-serif text-bio-dark mb-3">Organic Shapes</h3>
                    <p class="text-gray-600 text-sm leading-relaxed">Replacing rigid straight lines with curves, arches, and soft forms that mimic biological structures.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Interactive Breathing Section -->
    <section id="breathe" class="py-24 bg-bio-dark relative overflow-hidden text-white flex items-center justify-center min-h-[600px]">
        <!-- Background texture -->
        <div class="absolute inset-0 opacity-10" style="background-image: url('https://www.transparenttextures.com/patterns/cubes.png');"></div>
        
        <div class="container mx-auto px-6 text-center relative z-10">
            <h2 class="text-3xl md:text-4xl font-serif mb-2">Digital Sanctuary</h2>
            <p class="text-bio-pale/80 mb-12">Take a moment to sync your rhythm with nature.</p>

            <!-- Breathing App Circle -->
            <div class="relative w-64 h-64 mx-auto flex items-center justify-center">
                <!-- Animated outer rings -->
                <div id="breathe-ring-1" class="absolute inset-0 border border-white/20 rounded-full scale-100 transition-transform duration-[4000ms] ease-in-out"></div>
                <div id="breathe-ring-2" class="absolute inset-0 bg-white/5 rounded-full scale-75 blur-md transition-transform duration-[4000ms] ease-in-out delay-75"></div>
                
                <!-- Main Circle -->
                <div id="breathe-circle" class="w-48 h-48 bg-gradient-to-br from-bio-light to-bio-med rounded-full flex items-center justify-center shadow-[0_0_40px_rgba(141,170,145,0.4)] transition-all duration-[4000ms] ease-in-out">
                    <span id="breathe-text" class="text-2xl font-serif font-light tracking-widest uppercase">Inhale</span>
                </div>
            </div>

            <button onclick="toggleBreathing()" id="breathe-btn" class="mt-12 border border-white/30 px-8 py-2 rounded-full hover:bg-white hover:text-bio-dark transition-all">
                Pause
            </button>
        </div>
    </section>

    <!-- Gallery Section -->
    <section id="gallery" class="py-24 bg-sand">
        <div class="container mx-auto px-6">
            <div class="flex flex-col md:flex-row justify-between items-end mb-12">
                <div>
                    <h2 class="text-3xl md:text-4xl font-serif text-bio-dark mb-2">Textures of Earth</h2>
                    <p class="text-gray-600">Visual harmony found in the details.</p>
                </div>
                <div class="hidden md:block">
                    <i data-lucide="flower-2" class="text-bio-light w-10 h-10 animate-spin-slow"></i>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 auto-rows-[200px]">
                <!-- Masonry-style grid layout -->
                <div class="md:col-span-2 md:row-span-2 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1518531933037-91b2f5f229cc?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Nature texture">
                    <div class="absolute inset-0 bg-black/20 group-hover:bg-black/10 transition-colors"></div>
                    <div class="absolute bottom-6 left-6 text-white opacity-0 group-hover:opacity-100 transition-opacity">
                        <p class="font-serif text-lg">Forest Light</p>
                    </div>
                </div>

                <div class="md:col-span-1 md:row-span-1 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1596323605872-4632bbe4f62d?ixlib=rb-4.0.3&auto=format&fit=crop&w=500&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Leaf detail">
                </div>

                <div class="md:col-span-1 md:row-span-2 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1502082553048-f009c37129b9?ixlib=rb-4.0.3&auto=format&fit=crop&w=500&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Tree rings">
                </div>

                <div class="md:col-span-1 md:row-span-1 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1518173946687-a4c8892bbd9f?ixlib=rb-4.0.3&auto=format&fit=crop&w=500&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Water ripples">
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="bg-bio-dark text-bio-pale py-16 relative">
        <div class="container mx-auto px-6 grid md:grid-cols-4 gap-12 relative z-10">
            <div class="col-span-1 md:col-span-2">
                <div class="flex items-center gap-2 mb-6">
                    <i data-lucide="leaf" class="h-6 w-6"></i>
                    <span class="text-2xl font-serif font-medium tracking-wide">Verdant</span>
                </div>
                <p class="max-w-sm text-bio-light/80 leading-relaxed">
                    Designing for a world where nature and technology live in balance. Creating spaces that breathe, heal, and inspire.
                </p>
            </div>
            
            <div>
                <h4 class="font-serif text-lg mb-6 text-white">Explore</h4>
                <ul class="space-y-4 text-bio-light/80">
                    <li><a href="#" class="hover:text-white transition-colors">Philosophy</a></li>
                    <li><a href="#" class="hover:text-white transition-colors">Case Studies</a></li>
                    <li><a href="#" class="hover:text-white transition-colors">Materials</a></li>
                    <li><a href="#" class="hover:text-white transition-colors">Contact</a></li>
                </ul>
            </div>

            <div>
                <h4 class="font-serif text-lg mb-6 text-white">Newsletter</h4>
                <div class="flex flex-col gap-4">
                    <input type="email" placeholder="Your email address" class="bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-white placeholder-bio-light/50 focus:outline-none focus:border-bio-light">
                    <button class="bg-bio-light text-bio-dark font-semibold py-3 rounded-lg hover:bg-white transition-colors">
                        Subscribe
                    </button>
                </div>
            </div>
        </div>
        
        <div class="mt-16 text-center text-sm text-bio-light/40">
            &copy; 2023 Verdant Design Studio. All rights reserved.
        </div>
    </footer>

    <!-- JavaScript Functionality -->
    <script>
        // Initialize Lucide Icons
        lucide.createIcons();

        // 1. Navigation Scroll Effect
        const navbar = document.getElementById('navbar');
        window.addEventListener('scroll', () => {
            if (window.scrollY > 50) {
                navbar.classList.add('shadow-md');
                navbar.classList.remove('py-4');
                navbar.classList.add('py-2');
            } else {
                navbar.classList.remove('shadow-md');
                navbar.classList.remove('py-2');
                navbar.classList.add('py-4');
            }
        });

        // 2. Breathing Exercise Logic
        let isBreathingActive = true;
        const breatheText = document.getElementById('breathe-text');
        const breatheCircle = document.getElementById('breathe-circle');
        const breatheRing1 = document.getElementById('breathe-ring-1');
        const breatheRing2 = document.getElementById('breathe-ring-2');
        const breatheBtn = document.getElementById('breathe-btn');

        // Initial State
        let breathingState = 'inhale'; // inhale, hold, exhale, hold

        function breathingCycle() {
            if (!isBreathingActive) return;

            if (breathingState === 'inhale') {
                breatheText.innerText = 'Exhale';
                breatheText.style.opacity = '0.7';
                
                // Visuals for Exhale (Contract)
                breatheCircle.style.transform = 'scale(0.8)';
                breatheRing1.style.transform = 'scale(0.9)';
                breatheRing2.style.transform = 'scale(0.85)';
                
                breathingState = 'exhale';
            } else {
                breatheText.innerText = 'Inhale';
                breatheText.style.opacity = '1';
                
                // Visuals for Inhale (Expand)
                breatheCircle.style.transform = 'scale(1.2)';
                breatheRing1.style.transform = 'scale(1.5)';
                breatheRing2.style.transform = 'scale(1.3)';
                
                breathingState = 'inhale';
            }
        }

        // Run the cycle every 4 seconds
        let breatheInterval = setInterval(breathingCycle, 4000);

        function toggleBreathing() {
            isBreathingActive = !isBreathingActive;
            
            if (isBreathingActive) {
                breatheBtn.innerText = 'Pause';
                breatheBtn.classList.remove('bg-white', 'text-bio-dark');
                // Restart interval
                breathingCycle(); // immediate trigger
                clearInterval(breatheInterval);
                breatheInterval = setInterval(breathingCycle, 4000);
            } else {
                breatheBtn.innerText = 'Resume';
                breatheBtn.classList.add('bg-white', 'text-bio-dark');
                clearInterval(breatheInterval);
            }
        }

        // 3. Smooth Reveal on Scroll (Intersection Observer)
        const observerOptions = {
            threshold: 0.1,
            rootMargin: "0px 0px -50px 0px"
        };

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('opacity-100', 'translate-y-0');
                    entry.target.classList.remove('opacity-0', 'translate-y-10');
                    observer.unobserve(entry.target);
                }
            });
        }, observerOptions);

        // Select elements to animate
        document.querySelectorAll('h2, .group, p').forEach((el) => {
            el.classList.add('transition-all', 'duration-1000', 'opacity-0', 'translate-y-10');
            observer.observe(el);
        });

    </script>
</body>
</html>


<!DOCTYPE html>
<html lang="en" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🌿 EDITED FROM PUBLIC - Verdant</title>
    
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    
    <!-- Google Fonts: Nunito (Rounded Sans) and Lora (Serif) -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,500;1,400&family=Nunito:wght@300;400;600;700&display=swap" rel="stylesheet">
    
    <!-- Lucide Icons -->
    <script src="https://unpkg.com/lucide@latest"></script>

    <!-- Tailwind Config for Custom Colors/Fonts -->
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'bio-dark': '#2C4C3B',
                        'bio-med': '#4F775D',
                        'bio-light': '#8DAA91',
                        'bio-pale': '#E8EFE6',
                        'earth': '#8B735B',
                        'sand': '#F5F2EB',
                    },
                    fontFamily: {
                        sans: ['Nunito', 'sans-serif'],
                        serif: ['Lora', 'serif'],
                    },
                    animation: {
                        'float': 'float 6s ease-in-out infinite',
                        'breathe': 'breathe 8s ease-in-out infinite',
                        'sway': 'sway 4s ease-in-out infinite alternate',
                    },
                    keyframes: {
                        float: {
                            '0%, 100%': { transform: 'translateY(0)' },
                            '50%': { transform: 'translateY(-20px)' },
                        },
                        breathe: {
                            '0%, 100%': { transform: 'scale(1)' },
                            '50%': { transform: 'scale(1.2)' },
                        },
                        sway: {
                            '0%': { transform: 'rotate(-2deg)' },
                            '100%': { transform: 'rotate(2deg)' },
                        }
                    }
                }
            }
        }
    </script>

    <style>
        /* Custom Styles for Organic Shapes & Glassmorphism */
        body {
            background-color: #F5F2EB;
            color: #2C4C3B;
            overflow-x: hidden;
        }

        .glass-panel {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.3);
        }

        .glass-nav {
            background: rgba(245, 242, 235, 0.85);
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }

        .organic-shape-1 {
            border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%;
        }
        
        .organic-shape-2 {
            border-radius: 30% 70% 70% 30% / 30% 30% 70% 70%;
        }

        .text-shadow-sm {
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        /* Hide scrollbar for cleaner look but allow scroll */
        ::-webkit-scrollbar {
            width: 8px;
        }
        ::-webkit-scrollbar-track {
            background: #F5F2EB; 
        }
        ::-webkit-scrollbar-thumb {
            background: #8DAA91; 
            border-radius: 10px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #4F775D; 
        }

        .leaf-mask {
            mask-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath fill='%23FF0066' d='M44.7,-76.4C58.9,-69.2,71.8,-59.1,81.6,-46.6C91.4,-34.1,98.1,-19.2,95.8,-4.9C93.5,9.3,82.2,22.9,71.4,34.6C60.6,46.3,50.3,56.1,38.6,63.6C26.9,71.1,13.8,76.3,0.3,75.8C-13.2,75.3,-26.1,69.1,-37.9,61.6C-49.7,54.1,-60.4,45.3,-69.4,34.3C-78.4,23.3,-85.7,10.1,-84.9,-2.7C-84.1,-15.5,-75.2,-27.9,-65.1,-38.3C-55,-48.7,-43.7,-57.1,-31.6,-65.7C-19.5,-74.3,-6.6,-83.1,3.2,-88.6C13,-94.1,26,-96.3,44.7,-76.4Z' transform='translate(100 100)' /%3E%3C/svg%3E");
            mask-size: contain;
            mask-repeat: no-repeat;
            mask-position: center;
        }
    </style>
  <link rel="stylesheet" href="/assets/index-xfgqKiMF.css">
</head>
<body class="antialiased">

    <!-- Navigation -->
    <nav id="navbar" class="fixed w-full z-50 px-6 py-4 glass-nav">
        <div class="max-w-7xl mx-auto flex justify-between items-center">
            <div class="flex items-center gap-2">
                <i data-lucide="leaf" class="text-bio-dark h-6 w-6"></i>
                <span class="text-xl font-serif font-medium text-bio-dark tracking-wide">Verdant</span>
            </div>
            <div class="hidden md:flex gap-8 text-bio-dark font-medium">
                <a href="#home" class="hover:text-bio-light transition-colors">Sanctuary</a>
                <a href="#benefits" class="hover:text-bio-light transition-colors">Elements</a>
                <a href="#breathe" class="hover:text-bio-light transition-colors">Experience</a>
                <a href="#gallery" class="hover:text-bio-light transition-colors">Visuals</a>
            </div>
            <button class="bg-bio-dark text-white px-6 py-2 rounded-full hover:bg-bio-med transition-all duration-300 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5">
                Connect
            </button>
        </div>
    </nav>

    <!-- Hero Section -->
    <header id="home" class="relative min-h-screen flex items-center justify-center pt-20 overflow-hidden">
        <!-- Abstract Background Blobs -->
        <div class="absolute top-20 left-[-10%] w-[500px] h-[500px] bg-bio-light/20 rounded-full blur-3xl animate-float"></div>
        <div class="absolute bottom-20 right-[-5%] w-[400px] h-[400px] bg-green-200/30 rounded-full blur-3xl animate-float" style="animation-delay: 2s;"></div>

        <div class="container mx-auto px-6 relative z-10 grid md:grid-cols-2 gap-12 items-center">
            <div class="space-y-6">
                <div class="inline-block px-4 py-1 rounded-full bg-bio-light/20 text-bio-dark text-sm font-semibold tracking-wider uppercase mb-2">
                    Nature &bull; Architecture &bull; Wellness
                </div>
                <h1 class="text-5xl md:text-7xl font-serif text-bio-dark leading-tight">
                    Bring the <span class="italic text-bio-med">outside</span>, <br>inside.
                </h1>
                <p class="text-lg text-gray-600 max-w-lg leading-relaxed">
                    Biophilic design integrates natural elements into digital and physical spaces to reduce stress, enhance creativity, and improve well-being.
                </p>
                <div class="flex gap-4 pt-4">
                    <a href="#breathe" class="bg-bio-dark text-white px-8 py-3 rounded-full hover:bg-bio-med transition-colors shadow-lg flex items-center gap-2">
                        Start Journey <i data-lucide="arrow-right" class="w-4 h-4"></i>
                    </a>
                    <button class="px-8 py-3 rounded-full border border-bio-dark text-bio-dark hover:bg-bio-dark/5 transition-colors">
                        Learn More
                    </button>
                </div>
            </div>
            <div class="relative h-[500px] flex items-center justify-center">
                <!-- Image masked with organic shape -->
                <div class="relative w-full h-full">
                     <img 
                        src="https://images.unsplash.com/photo-1463936575829-25148e1db1b8?ixlib=rb-4.0.3&auto=format&fit=crop&w=1000&q=80" 
                        alt="Succulents and nature textures" 
                        class="absolute inset-0 w-full h-full object-cover organic-shape-1 shadow-2xl animate-sway"
                    >
                </div>
            </div>
        </div>

        <!-- Wave SVG Separator -->
        <div class="absolute bottom-0 w-full leading-none">
            <svg class="block w-full h-24 md:h-48 text-white" viewBox="0 0 1440 320" preserveAspectRatio="none">
                <path fill="#ffffff" fill-opacity="1" d="M0,224L48,213.3C96,203,192,181,288,181.3C384,181,480,203,576,224C672,245,768,267,864,261.3C960,256,1056,224,1152,197.3C1248,171,1344,149,1392,138.7L1440,128L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"></path>
            </svg>
        </div>
    </header>

    <!-- Principles Section -->
    <section id="benefits" class="py-24 bg-white relative">
        <div class="container mx-auto px-6">
            <div class="text-center mb-16">
                <h2 class="text-3xl md:text-4xl font-serif text-bio-dark mb-4">Core Principles</h2>
                <p class="text-gray-600 max-w-2xl mx-auto">Connecting the human biological need for nature with the modern built environment through organic textures, light, and shapes.</p>
            </div>

            <div class="grid md:grid-cols-3 gap-8">
                <!-- Card 1 -->
                <div class="group p-8 rounded-[2rem] bg-sand hover:bg-bio-pale transition-colors duration-500 cursor-pointer">
                    <div class="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm text-bio-dark group-hover:scale-110 transition-transform">
                        <i data-lucide="sun" class="w-7 h-7"></i>
                    </div>
                    <h3 class="text-xl font-serif text-bio-dark mb-3">Natural Light</h3>
                    <p class="text-gray-600 text-sm leading-relaxed">Mimicking circadian rhythms through dynamic lighting to improve mood, energy, and sleep quality.</p>
                </div>

                <!-- Card 2 -->
                <div class="group p-8 rounded-[2rem] bg-sand hover:bg-bio-pale transition-colors duration-500 cursor-pointer">
                    <div class="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm text-bio-dark group-hover:scale-110 transition-transform">
                        <i data-lucide="sprout" class="w-7 h-7"></i>
                    </div>
                    <h3 class="text-xl font-serif text-bio-dark mb-3">Living Systems</h3>
                    <p class="text-gray-600 text-sm leading-relaxed">Direct presence of nature—plants, water features, and green walls—to purify air and reduce stress.</p>
                </div>

                <!-- Card 3 -->
                <div class="group p-8 rounded-[2rem] bg-sand hover:bg-bio-pale transition-colors duration-500 cursor-pointer">
                    <div class="w-14 h-14 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm text-bio-dark group-hover:scale-110 transition-transform">
                        <i data-lucide="wind" class="w-7 h-7"></i>
                    </div>
                    <h3 class="text-xl font-serif text-bio-dark mb-3">Organic Shapes</h3>
                    <p class="text-gray-600 text-sm leading-relaxed">Replacing rigid straight lines with curves, arches, and soft forms that mimic biological structures.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Interactive Breathing Section -->
    <section id="breathe" class="py-24 bg-bio-dark relative overflow-hidden text-white flex items-center justify-center min-h-[600px]">
        <!-- Background texture -->
        <div class="absolute inset-0 opacity-10" style="background-image: url('https://www.transparenttextures.com/patterns/cubes.png');"></div>
        
        <div class="container mx-auto px-6 text-center relative z-10">
            <h2 class="text-3xl md:text-4xl font-serif mb-2">Digital Sanctuary</h2>
            <p class="text-bio-pale/80 mb-12">Take a moment to sync your rhythm with nature.</p>

            <!-- Breathing App Circle -->
            <div class="relative w-64 h-64 mx-auto flex items-center justify-center">
                <!-- Animated outer rings -->
                <div id="breathe-ring-1" class="absolute inset-0 border border-white/20 rounded-full scale-100 transition-transform duration-[4000ms] ease-in-out"></div>
                <div id="breathe-ring-2" class="absolute inset-0 bg-white/5 rounded-full scale-75 blur-md transition-transform duration-[4000ms] ease-in-out delay-75"></div>
                
                <!-- Main Circle -->
                <div id="breathe-circle" class="w-48 h-48 bg-gradient-to-br from-bio-light to-bio-med rounded-full flex items-center justify-center shadow-[0_0_40px_rgba(141,170,145,0.4)] transition-all duration-[4000ms] ease-in-out">
                    <span id="breathe-text" class="text-2xl font-serif font-light tracking-widest uppercase">Inhale</span>
                </div>
            </div>

            <button onclick="toggleBreathing()" id="breathe-btn" class="mt-12 border border-white/30 px-8 py-2 rounded-full hover:bg-white hover:text-bio-dark transition-all">
                Pause
            </button>
        </div>
    </section>

    <!-- Gallery Section -->
    <section id="gallery" class="py-24 bg-sand">
        <div class="container mx-auto px-6">
            <div class="flex flex-col md:flex-row justify-between items-end mb-12">
                <div>
                    <h2 class="text-3xl md:text-4xl font-serif text-bio-dark mb-2">Textures of Earth</h2>
                    <p class="text-gray-600">Visual harmony found in the details.</p>
                </div>
                <div class="hidden md:block">
                    <i data-lucide="flower-2" class="text-bio-light w-10 h-10 animate-spin-slow"></i>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 auto-rows-[200px]">
                <!-- Masonry-style grid layout -->
                <div class="md:col-span-2 md:row-span-2 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1518531933037-91b2f5f229cc?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Nature texture">
                    <div class="absolute inset-0 bg-black/20 group-hover:bg-black/10 transition-colors"></div>
                    <div class="absolute bottom-6 left-6 text-white opacity-0 group-hover:opacity-100 transition-opacity">
                        <p class="font-serif text-lg">Forest Light</p>
                    </div>
                </div>

                <div class="md:col-span-1 md:row-span-1 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1596323605872-4632bbe4f62d?ixlib=rb-4.0.3&auto=format&fit=crop&w=500&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Leaf detail">
                </div>

                <div class="md:col-span-1 md:row-span-2 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1502082553048-f009c37129b9?ixlib=rb-4.0.3&auto=format&fit=crop&w=500&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Tree rings">
                </div>

                <div class="md:col-span-1 md:row-span-1 relative group overflow-hidden rounded-[2rem]">
                    <img src="https://images.unsplash.com/photo-1518173946687-a4c8892bbd9f?ixlib=rb-4.0.3&auto=format&fit=crop&w=500&q=80" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="Water ripples">
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="bg-bio-dark text-bio-pale py-16 relative">
        <div class="container mx-auto px-6 grid md:grid-cols-4 gap-12 relative z-10">
            <div class="col-span-1 md:col-span-2">
                <div class="flex items-center gap-2 mb-6">
                    <i data-lucide="leaf" class="h-6 w-6"></i>
                    <span class="text-2xl font-serif font-medium tracking-wide">Verdant</span>
                </div>
                <p class="max-w-sm text-bio-light/80 leading-relaxed">
                    Designing for a world where nature and technology live in balance. Creating spaces that breathe, heal, and inspire.
                </p>
            </div>
            
            <div>
                <h4 class="font-serif text-lg mb-6 text-white">Explore</h4>
                <ul class="space-y-4 text-bio-light/80">
                    <li><a href="#" class="hover:text-white transition-colors">Philosophy</a></li>
                    <li><a href="#" class="hover:text-white transition-colors">Case Studies</a></li>
                    <li><a href="#" class="hover:text-white transition-colors">Materials</a></li>
                    <li><a href="#" class="hover:text-white transition-colors">Contact</a></li>
                </ul>
            </div>

            <div>
                <h4 class="font-serif text-lg mb-6 text-white">Newsletter</h4>
                <div class="flex flex-col gap-4">
                    <input type="email" placeholder="Your email address" class="bg-white/10 border border-white/20 rounded-lg px-4 py-3 text-white placeholder-bio-light/50 focus:outline-none focus:border-bio-light">
                    <button class="bg-bio-light text-bio-dark font-semibold py-3 rounded-lg hover:bg-white transition-colors">
                        Subscribe
                    </button>
                </div>
            </div>
        </div>
        
        <div class="mt-16 text-center text-sm text-bio-light/40">
            &copy; 2023 Verdant Design Studio. All rights reserved.
        </div>
    </footer>

    <!-- JavaScript Functionality -->
    <script>
        // Initialize Lucide Icons
        lucide.createIcons();

        // 1. Navigation Scroll Effect
        const navbar = document.getElementById('navbar');
        window.addEventListener('scroll', () => {
            if (window.scrollY > 50) {
                navbar.classList.add('shadow-md');
                navbar.classList.remove('py-4');
                navbar.classList.add('py-2');
            } else {
                navbar.classList.remove('shadow-md');
                navbar.classList.remove('py-2');
                navbar.classList.add('py-4');
            }
        });

        // 2. Breathing Exercise Logic
        let isBreathingActive = true;
        const breatheText = document.getElementById('breathe-text');
        const breatheCircle = document.getElementById('breathe-circle');
        const breatheRing1 = document.getElementById('breathe-ring-1');
        const breatheRing2 = document.getElementById('breathe-ring-2');
        const breatheBtn = document.getElementById('breathe-btn');

        // Initial State
        let breathingState = 'inhale'; // inhale, hold, exhale, hold

        function breathingCycle() {
            if (!isBreathingActive) return;

            if (breathingState === 'inhale') {
                breatheText.innerText = 'Exhale';
                breatheText.style.opacity = '0.7';
                
                // Visuals for Exhale (Contract)
                breatheCircle.style.transform = 'scale(0.8)';
                breatheRing1.style.transform = 'scale(0.9)';
                breatheRing2.style.transform = 'scale(0.85)';
                
                breathingState = 'exhale';
            } else {
                breatheText.innerText = 'Inhale';
                breatheText.style.opacity = '1';
                
                // Visuals for Inhale (Expand)
                breatheCircle.style.transform = 'scale(1.2)';
                breatheRing1.style.transform = 'scale(1.5)';
                breatheRing2.style.transform = 'scale(1.3)';
                
                breathingState = 'inhale';
            }
        }

        // Run the cycle every 4 seconds
        let breatheInterval = setInterval(breathingCycle, 4000);

        function toggleBreathing() {
            isBreathingActive = !isBreathingActive;
            
            if (isBreathingActive) {
                breatheBtn.innerText = 'Pause';
                breatheBtn.classList.remove('bg-white', 'text-bio-dark');
                // Restart interval
                breathingCycle(); // immediate trigger
                clearInterval(breatheInterval);
                breatheInterval = setInterval(breathingCycle, 4000);
            } else {
                breatheBtn.innerText = 'Resume';
                breatheBtn.classList.add('bg-white', 'text-bio-dark');
                clearInterval(breatheInterval);
            }
        }

        // 3. Smooth Reveal on Scroll (Intersection Observer)
        const observerOptions = {
            threshold: 0.1,
            rootMargin: "0px 0px -50px 0px"
        };

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('opacity-100', 'translate-y-0');
                    entry.target.classList.remove('opacity-0', 'translate-y-10');
                    observer.unobserve(entry.target);
                }
            });
        }, observerOptions);

        // Select elements to animate
        document.querySelectorAll('h2, .group, p').forEach((el) => {
            el.classList.add('transition-all', 'duration-1000', 'opacity-0', 'translate-y-10');
            observer.observe(el);
        });

    </script>
</body>
</html>
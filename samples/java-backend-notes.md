# Java 后端学习笔记

## 一、JVM 内存结构与垃圾回收

Java 虚拟机在运行时把内存划分为线程私有的程序计数器、虚拟机栈、本地方法栈，
以及线程共享的堆和方法区（JDK 8 之后由元空间实现）。

堆是垃圾回收的主要区域，按分代假说划分为新生代与老年代。新生代又分为
Eden、Survivor0、Survivor1 三个区域，默认比例 8:1:1。

### 1.1 如何判断对象已死

主流 JVM 使用可达性分析算法，从一系列称为 GC Roots 的对象出发向下搜索，
不可达的对象才会被回收。可作为 GC Roots 的对象包括：

- 虚拟机栈中局部变量表引用的对象
- 方法区中类静态属性引用的对象
- 方法区中常量引用的对象
- 本地方法栈中 JNI 引用的对象

引用计数法无法解决循环引用问题，因此不是 HotSpot 的选择。

### 1.2 垃圾回收算法

标记-清除算法实现简单，但会产生大量内存碎片。
复制算法把内存一分为二，只使用其中一半，回收时把存活对象复制到另一半，
代价是可用内存减半，适合对象存活率低的新生代。
标记-整理算法让存活对象向一端移动，适合对象存活率高的老年代。

### 1.3 垃圾收集器

Serial 是单线程收集器，适合客户端场景。
ParNew 是 Serial 的多线程版本。
Parallel Scavenge 关注吞吐量，目标是达到可控的吞吐量。
CMS 以最短停顿时间为目标，使用标记-清除，会产生碎片。
G1 把堆划分为多个 Region，可以预测停顿时间，是 JDK 9 之后的默认收集器。
ZGC 和 Shenandoah 追求极低停顿，停顿时间与堆大小基本无关。

选择收集器的经验：延迟敏感选 G1 或 ZGC，吞吐优先选 Parallel。

## 二、Java 集合框架

ArrayList 基于动态数组，随机访问 O(1)，中间插入删除需要移动元素。
LinkedList 基于双向链表，头尾插入 O(1)，随机访问 O(n)。
实际项目中 ArrayList 使用频率远高于 LinkedList。

HashMap 在 JDK 8 之后采用数组 + 链表 + 红黑树的结构。
链表长度超过 8 且数组长度不小于 64 时，链表转为红黑树，把最坏查询复杂度
从 O(n) 降到 O(log n)。默认负载因子 0.75，容量总是 2 的幂，
这样可以用位运算代替取模来计算下标。

ConcurrentHashMap 在 JDK 8 中使用 CAS + synchronized 只锁住单个桶，
并发度远高于 JDK 7 的分段锁。

## 三、多线程与并发

创建线程的方式：继承 Thread、实现 Runnable、实现 Callable 配合 FutureTask、
使用线程池。生产环境禁止直接 new Thread，必须使用线程池。

线程池的核心参数：corePoolSize、maximumPoolSize、keepAliveTime、workQueue、
threadFactory、handler。执行流程是：核心线程未满则创建核心线程，
核心线程满则入队，队列满则创建非核心线程，再满则执行拒绝策略。

常见的拒绝策略：AbortPolicy 抛异常、CallerRunsPolicy 由调用线程执行、
DiscardPolicy 静默丢弃、DiscardOldestPolicy 丢弃最老任务。

synchronized 在 JDK 6 之后引入偏向锁、轻量级锁、重量级锁的升级过程。
volatile 保证可见性和有序性，但不保证原子性。
CAS 是乐观锁的实现基础，存在 ABA 问题，可以用版本号或 AtomicStampedReference 解决。

AQS 是 ReentrantLock、Semaphore、CountDownLatch 的共同基础，
内部维护一个 volatile int state 和一个 CLH 等待队列。

## 四、待办事项

- TODO: 完成 JVM 调优参数的整理，包括 -Xms、-Xmx、-XX:MetaspaceSize
- TODO: 手写一个基于 AQS 的简易锁，理解独占模式与共享模式的区别
- 待办：整理一次线上 OOM 排查流程，形成可复用的检查清单
- 下一步：为项目补充线程池监控指标

## 五、常见面试问题

1. 为什么 HashMap 的容量必须是 2 的幂？
2. G1 与 CMS 的核心区别是什么？
3. 线程池的队列满了之后会发生什么？
4. volatile 能保证原子性吗？为什么？
5. 什么是伪共享，如何避免？

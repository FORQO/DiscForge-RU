/* DiscForge RU - MIT. No network, filesystem writes or privileged operations. */
import java.awt.Color;
import java.awt.Container;
import java.awt.Graphics;
import java.awt.Image;
import java.awt.image.ImageObserver;
import java.awt.Toolkit;
import java.awt.event.KeyEvent;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.util.Properties;
import javax.media.ControllerEvent;
import javax.media.ControllerListener;
import javax.media.EndOfMediaEvent;
import javax.media.Manager;
import javax.media.MediaLocator;
import javax.media.Player;
import javax.tv.xlet.Xlet;
import javax.tv.xlet.XletContext;
import org.bluray.media.PlaybackControl;
import org.bluray.media.PrimaryAudioControl;
import org.bluray.media.SubtitlingControl;
import org.bluray.ui.event.HRcEvent;
import org.dvb.event.EventManager;
import org.dvb.event.UserEvent;
import org.dvb.event.UserEventListener;
import org.dvb.event.UserEventRepository;
import org.havi.ui.HScene;
import org.havi.ui.HSceneFactory;

public final class DiscForgeXlet extends Container implements Xlet, UserEventListener, ControllerListener, Runnable {
    private static final long serialVersionUID = 1L;
    private HScene scene;
    private final Properties config = new Properties();
    private Image background;
    private Image highlight;
    private int highlightX, highlightY;
    private Player player;
    private int page, focus;
    private String movie = "";
    private int selectedAudio = 1;
    private int selectedSubtitle = 0;
    private volatile boolean audioPopup;
    private volatile boolean playing, stopped, busy;
    private String pending;
    private String error;
    private Thread worker;

    public void initXlet(XletContext context) throws javax.tv.xlet.XletStateChangeException {
        try {
            InputStream in = getClass().getResourceAsStream("/menu.properties");
            if (in == null) throw new Exception("Missing menu configuration");
            try { config.load(in); } finally { in.close(); }
            scene = HSceneFactory.getInstance().getDefaultHScene();
            setBounds(0, 0, 1920, 1080);
            scene.setLayout(null);
            scene.add(this);
            UserEventRepository keys = new UserEventRepository("DiscForge menu");
            keys.addAllArrowKeys();
            keys.addKey(KeyEvent.VK_ENTER);
            keys.addKey(KeyEvent.VK_ESCAPE);
            keys.addKey(KeyEvent.VK_BACK_SPACE);
            keys.addKey(461); // remote Back
            keys.addKey(HRcEvent.VK_POPUP_MENU);
            EventManager.getInstance().addUserEventListener(this, keys);
            loadPage(0);
            worker = new Thread(this, "DiscForge playback");
            worker.start();
        } catch (Exception e) {
            throw new javax.tv.xlet.XletStateChangeException(e.toString());
        }
    }

    public void startXlet() { scene.setVisible(true); setVisible(true); requestFocus(); repaint(); }
    public void pauseXlet() { queue("home"); }
    public void destroyXlet(boolean unconditional) {
        stopped = true;
        synchronized (this) { notifyAll(); }
        EventManager.getInstance().removeUserEventListener(this);
        if (worker != null) {
            try { worker.join(3000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
        closePlayer();
        if (background != null) background.flush();
        if (highlight != null) highlight.flush();
        if (scene != null) { scene.remove(this); HSceneFactory.getInstance().dispose(scene); }
    }

    private synchronized void queue(String action) {
        if (!stopped && (pending == null || action.equals("home"))) { pending = action; notifyAll(); }
    }

    private Image loadImage(String name) throws Exception {
        // Read inside the Xlet context. AWT's asynchronous URL fetcher may lack
        // that context on BD-J players, especially after entering a new page.
        InputStream input = getClass().getResourceAsStream("/" + name);
        if (input == null) throw new Exception("Missing menu image: " + name);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try {
            byte[] block = new byte[8192];
            int count;
            while ((count = input.read(block)) != -1) {
                if (bytes.size() + count > 16777216) throw new Exception("Menu image too large");
                bytes.write(block, 0, count);
            }
        } finally { input.close(); }
        Image image = Toolkit.getDefaultToolkit().createImage(bytes.toByteArray());
        Toolkit toolkit = Toolkit.getDefaultToolkit();
        toolkit.prepareImage(image, -1, -1, null);
        long deadline = System.currentTimeMillis() + 10000;
        int flags;
        do {
            flags = toolkit.checkImage(image, -1, -1, null);
            // Some BD-J toolkits report ABORT after delivering ALLBITS. A fully
            // decoded image is usable; MediaTracker treats ABORT as failure.
            if ((flags & ImageObserver.ALLBITS) != 0) return image;
            if ((flags & ImageObserver.ERROR) != 0) break;
            Thread.sleep(10);
        } while (!stopped && System.currentTimeMillis() < deadline);
        image.flush();
        throw new Exception("Menu image unavailable: " + name + " flags=" + flags);
    }

    private void loadPage(int number) throws Exception {
        String nextMovie = config.getProperty("page." + number + ".movie", movie);
        if (!nextMovie.equals(movie)) {
            movie = nextMovie;
            selectedAudio = 1;
            selectedSubtitle = 0;
        }
        Image image = loadImage("page" + number + ".png");
        Image previous = background;
        background = image;
        page = number; focus = 0;
        int count = Integer.parseInt(config.getProperty("page." + page + ".count"));
        for (int i = 0; i < count; i++) {
            if (("sub:" + selectedSubtitle).equals(config.getProperty("page." + page + "." + i))) focus = i;
            if (("audio:" + selectedAudio).equals(config.getProperty("page." + page + "." + i))) focus = i;
        }
        loadFocus();
        System.out.println("DiscForge: menu page " + page);
        if (previous != null) previous.flush();
        repaint();
    }

    private void loadFocus() throws Exception {
        if (highlight != null) { highlight.flush(); highlight = null; }
        if (Integer.parseInt(config.getProperty("page." + page + ".count")) == 0) return;
        String key = "page." + page + "." + focus;
        Image image = loadImage(config.getProperty(key + ".focus"));
        highlightX = Integer.parseInt(config.getProperty(key + ".focusX"));
        highlightY = Integer.parseInt(config.getProperty(key + ".focusY"));
        highlight = image;
    }

    public synchronized void userEventReceived(UserEvent event) {
        if (event.getType() != KeyEvent.KEY_PRESSED || stopped) return;
        int key = event.getCode();
        if (key == 461 || key == KeyEvent.VK_ESCAPE || key == KeyEvent.VK_BACK_SPACE || key == HRcEvent.VK_POPUP_MENU) {
            if (busy || pending != null) return;
            if (audioPopup) queue("resume");
            else if (playing && key == HRcEvent.VK_POPUP_MENU && (audioCount() > 0 || subtitleCount() > 0)) queue("audio-popup");
            else queue("home");
            return;
        }
        if (playing || busy || pending != null) return;
        int count = Integer.parseInt(config.getProperty("page." + page + ".count"));
        if (count == 0) return;
        if (key == KeyEvent.VK_DOWN) queue("focus:down");
        if (key == KeyEvent.VK_UP) queue("focus:up");
        if (key == KeyEvent.VK_RIGHT) queue("focus:right");
        if (key == KeyEvent.VK_LEFT) queue("focus:left");
        if (key == KeyEvent.VK_ENTER) queue(config.getProperty("page." + page + "." + focus));
    }

    private synchronized void closePlayer() {
        playing = false;
        audioPopup = false;
        if (player != null) {
            player.removeControllerListener(this);
            player.stop(); player.deallocate(); player.close(); player = null;
        }
    }

    private String movieProperty(String key, String fallback) {
        return config.getProperty("movie." + movie + "." + key, config.getProperty(key, fallback));
    }

    private int movieHome() { return Integer.parseInt(movieProperty("home", "0")); }

    private int audioCount() { return Integer.parseInt(movieProperty("audio.count", "0")); }

    private int subtitleCount() { return Integer.parseInt(movieProperty("subtitle.count", "0")); }

    private SubtitlingControl subtitleControl() throws Exception {
        SubtitlingControl control = (SubtitlingControl) player.getControl("org.bluray.media.SubtitlingControl");
        if (control == null) throw new Exception("Subtitle selection unavailable on this player");
        return control;
    }

    private void selectSubtitle(int number) throws Exception {
        if (number < 0 || number > subtitleCount()) throw new Exception("Invalid subtitle stream");
        if (player != null) {
            SubtitlingControl control = subtitleControl();
            if (number > 0) {
                int[] available = control.listAvailableStreamNumbers();
                boolean found = false;
                for (int i = 0; i < available.length; i++) if (available[i] == number) found = true;
                if (!found) throw new Exception("Subtitle stream unavailable: " + number);
                // Reassert display state for the new decoder, including players
                // that do not carry the old display flag across stream changes.
                control.setSubtitling(false);
                control.selectStreamNumber(number);
            }
            control.setSubtitling(number != 0);
            if (control.isSubtitlingOn() != (number != 0) ||
                (number > 0 && control.getCurrentStreamNumber() != number))
                throw new Exception("Player did not select subtitle stream " + number);
        }
        selectedSubtitle = number;
        System.out.println("DiskForge: selected subtitle " + number);
    }

    private PrimaryAudioControl audioControl() throws Exception {
        PrimaryAudioControl control = (PrimaryAudioControl) player.getControl("org.bluray.media.PrimaryAudioControl");
        if (control == null) throw new Exception("Audio selection unavailable on this player");
        return control;
    }

    private void selectAudio(int number) throws Exception {
        if (number < 1 || number > audioCount()) throw new Exception("Invalid audio stream");
        if (player != null) {
            PrimaryAudioControl control = audioControl();
            int[] available = control.listAvailableStreamNumbers();
            boolean found = false;
            for (int i = 0; i < available.length; i++) if (available[i] == number) found = true;
            if (!found) throw new Exception("Audio stream unavailable: " + number);
            control.selectStreamNumber(number);
            if (control.getCurrentStreamNumber() != number) throw new Exception("Player did not select audio stream " + number);
        }
        selectedAudio = number;
        System.out.println("DiscForge: selected primary audio " + number);
    }

    private void resumeMovie() throws Exception {
        if (player == null) throw new Exception("No movie to resume");
        if (player.setRate(1.0f) != 1.0f) throw new Exception("Player could not resume movie");
        audioPopup = false;
        playing = true;
        scene.setVisible(false);
        repaint();
        System.out.println("DiscForge: resumed movie at " + player.getMediaTime().getSeconds());
    }

    // Identical to menu_navigation.nearest_button: doubled centres, open
    // half-plane, distance / sideways displacement / stable index; no wrapping.
    static int nearestButton(int[][] rects, int current, String direction) {
        if (current < 0 || current >= rects.length) return current;
        boolean horizontal = direction.equals("left") || direction.equals("right");
        if (!horizontal && !direction.equals("up") && !direction.equals("down")) return current;
        int cx = 2 * rects[current][0] + rects[current][2];
        int cy = 2 * rects[current][1] + rects[current][3];
        int target = current, bestSideways = Integer.MAX_VALUE;
        long bestDistance = Long.MAX_VALUE;
        for (int i = 0; i < rects.length; i++) {
            int dx = 2 * rects[i][0] + rects[i][2] - cx;
            int dy = 2 * rects[i][1] + rects[i][3] - cy;
            int forward = horizontal ? dx : dy;
            if (direction.equals("left") || direction.equals("up")) forward = -forward;
            if (i == current || forward <= 0) continue;
            int sideways = Math.abs(horizontal ? dy : dx);
            long distance = (long) dx * dx + (long) dy * dy;
            if (distance < bestDistance || (distance == bestDistance && sideways < bestSideways)) {
                target = i; bestDistance = distance; bestSideways = sideways;
            }
        }
        return target;
    }

    private void moveFocus(String direction) throws Exception {
        int count = Integer.parseInt(config.getProperty("page." + page + ".count"));
        int[][] rects = new int[count][4];
        String[] fields = {"x", "y", "w", "h"};
        for (int i = 0; i < count; i++) {
            String prefix = "page." + page + "." + i + ".";
            for (int j = 0; j < 4; j++) rects[i][j] = Integer.parseInt(config.getProperty(prefix + fields[j]));
        }
        int target = nearestButton(rects, focus, direction);
        if (target != focus) { focus = target; loadFocus(); repaint(); }
    }

    public void run() {
        while (!stopped) {
            String action;
            synchronized (this) {
                while (pending == null && !stopped) {
                    try { wait(); } catch (InterruptedException e) { return; }
                }
                if (stopped) return;
                action = pending;
                pending = null;
                busy = true;
            }
            try {
                error = null;
                if (action.startsWith("focus:")) {
                    moveFocus(action.substring(6));
                }
                else if (action.equals("home")) {
                    int destination = playing || page != movieHome() ? movieHome() : 0;
                    if (!playing && config.getProperty("movies") != null && config.getProperty("page." + page + ".movie") == null) destination = 0;
                    closePlayer(); loadPage(destination); scene.setVisible(true);
                }
                else if (action.equals("resume") || (audioPopup && (action.equals("page:" + movieHome()) || action.equals("play:0")))) resumeMovie();
                else if (action.equals("audio-popup")) {
                    if (player.setRate(0.0f) != 0.0f) throw new Exception("Player could not pause for audio menu");
                    playing = false;
                    audioPopup = true;
                    if (audioCount() > 0) {
                        int current = audioControl().getCurrentStreamNumber();
                        if (current >= 1 && current <= audioCount()) selectedAudio = current;
                    }
                    if (subtitleCount() > 0) {
                        SubtitlingControl control = subtitleControl();
                        int current = control.isSubtitlingOn() ? control.getCurrentStreamNumber() : 0;
                        if (current >= 0 && current <= subtitleCount()) selectedSubtitle = current;
                    }
                    if (audioCount() > 0 && subtitleCount() > 0) loadPage(movieHome());
                    else if (subtitleCount() > 0)
                        loadPage(Integer.parseInt(movieProperty("subtitle.page", "0")) + selectedSubtitle / 5);
                    else loadPage(Integer.parseInt(movieProperty("audio.page", "0")) + (selectedAudio - 1) / 5);
                    scene.setVisible(true);
                    System.out.println("DiscForge: audio popup at " + player.getMediaTime().getSeconds());
                }
                else if (action.startsWith("sub:")) {
                    selectSubtitle(Integer.parseInt(action.substring(4)));
                    if (audioPopup) resumeMovie();
                    else loadPage(movieHome());
                }
                else if (action.startsWith("audio:")) {
                    selectAudio(Integer.parseInt(action.substring(6)));
                    if (audioPopup) resumeMovie();
                    else loadPage(movieHome());
                }
                else if (action.startsWith("page:")) loadPage(Integer.parseInt(action.substring(5)));
                else if (action.startsWith("play:")) {
                    closePlayer();
                    Player next = Manager.createPlayer(new MediaLocator("bd://1.PLAYLIST:" + movieProperty("playlist", "00000")));
                    System.out.println("DiscForge: playlist " + movieProperty("playlist", "00000"));
                    player = next;
                    next.prefetch();
                    long deadline = System.currentTimeMillis() + 15000;
                    while (next.getState() < Player.Prefetched && !stopped && System.currentTimeMillis() < deadline) Thread.sleep(50);
                    if (next.getState() < Player.Prefetched) throw new Exception("Player preparation timed out");
                    synchronized (this) {
                        if (stopped) { next.close(); return; }
                        player = next;
                        player.addControllerListener(this);
                        int mark = Integer.parseInt(action.substring(5));
                        PlaybackControl control = (PlaybackControl) player.getControl("org.bluray.media.PlaybackControl");
                        if (control == null) throw new Exception("Chapter control unavailable");
                        control.skipToMark(mark);
                        if (audioCount() > 0) selectAudio(selectedAudio);
                        if (subtitleCount() > 0) selectSubtitle(selectedSubtitle);
                        System.out.println("DiscForge: play chapter " + mark);
                        playing = true;
                        scene.setVisible(false);
                        repaint();
                        player.start();
                    }
                }
            } catch (Exception e) {
                // Keep a paused movie and its position available after an audio error.
                if (!audioPopup) closePlayer();
                error = e.toString();
                System.err.println("DiscForge: " + error);
                scene.setVisible(true); repaint();
            }
            finally { busy = false; }
        }
    }

    public void controllerUpdate(ControllerEvent event) {
        if (event instanceof EndOfMediaEvent && event.getSourceController() == player) queue("home");
    }

    public synchronized void paint(Graphics g) {
        if (playing) { g.clearRect(0, 0, 1920, 1080); return; }
        if (background != null) g.drawImage(background, 0, 0, this);
        if (highlight != null) g.drawImage(highlight, highlightX, highlightY, this);
        if (error != null) {
            g.setColor(Color.black); g.fillRect(100, 1000, 1720, 70);
            g.setColor(Color.white); g.drawString("Playback error. Back to retry: " + error, 120, 1040);
        }
    }
    public void update(Graphics g) { paint(g); }
}

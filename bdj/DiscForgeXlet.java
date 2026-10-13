/* DiscForge RU - MIT. No network, filesystem writes or privileged operations. */
import java.awt.Color;
import java.awt.Container;
import java.awt.Graphics;
import java.awt.Image;
import java.awt.MediaTracker;
import java.awt.Toolkit;
import java.awt.event.KeyEvent;
import java.io.InputStream;
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
    private int selectedAudio = 1;
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

    private synchronized void loadPage(int number) throws Exception {
        Image image = Toolkit.getDefaultToolkit().createImage(getClass().getResource("/page" + number + ".png"));
        MediaTracker tracker = new MediaTracker(this);
        tracker.addImage(image, 0);
        tracker.waitForAll(10000);
        if (tracker.isErrorAny() || !tracker.checkAll()) throw new Exception("Menu image unavailable");
        Image previous = background;
        background = image;
        page = number; focus = 0;
        int count = Integer.parseInt(config.getProperty("page." + page + ".count"));
        for (int i = 0; i < count; i++) {
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
        Image image = Toolkit.getDefaultToolkit().createImage(getClass().getResource("/" + config.getProperty(key + ".focus")));
        MediaTracker tracker = new MediaTracker(this);
        tracker.addImage(image, 0);
        tracker.waitForAll(10000);
        if (tracker.isErrorAny() || !tracker.checkAll()) { image.flush(); throw new Exception("Focus image unavailable"); }
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
            else if (playing && key == HRcEvent.VK_POPUP_MENU && audioCount() > 0) queue("audio-popup");
            else queue("home");
            return;
        }
        if (playing || busy || pending != null) return;
        int count = Integer.parseInt(config.getProperty("page." + page + ".count"));
        if (count == 0) return;
        int previousFocus = focus;
        if (key == KeyEvent.VK_DOWN || key == KeyEvent.VK_RIGHT) focus = (focus + 1) % count;
        if (key == KeyEvent.VK_UP || key == KeyEvent.VK_LEFT) focus = (focus + count - 1) % count;
        if (key == KeyEvent.VK_ENTER) queue(config.getProperty("page." + page + "." + focus));
        if (focus != previousFocus) {
            try { loadFocus(); } catch (Exception e) { error = e.toString(); }
        }
        repaint();
    }

    private synchronized void closePlayer() {
        playing = false;
        audioPopup = false;
        if (player != null) {
            player.removeControllerListener(this);
            player.stop(); player.deallocate(); player.close(); player = null;
        }
    }

    private int audioCount() { return Integer.parseInt(config.getProperty("audio.count", "0")); }

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
                if (action.equals("home")) { closePlayer(); loadPage(0); scene.setVisible(true); }
                else if (action.equals("resume") || (audioPopup && action.equals("page:0"))) resumeMovie();
                else if (action.equals("audio-popup")) {
                    if (player.setRate(0.0f) != 0.0f) throw new Exception("Player could not pause for audio menu");
                    playing = false;
                    audioPopup = true;
                    int current = audioControl().getCurrentStreamNumber();
                    if (current >= 1 && current <= audioCount()) selectedAudio = current;
                    int first = Integer.parseInt(config.getProperty("audio.page"));
                    loadPage(first + (selectedAudio - 1) / 5);
                    scene.setVisible(true);
                    System.out.println("DiscForge: audio popup at " + player.getMediaTime().getSeconds());
                }
                else if (action.startsWith("audio:")) {
                    selectAudio(Integer.parseInt(action.substring(6)));
                    if (audioPopup) resumeMovie();
                    else loadPage(0);
                }
                else if (action.startsWith("page:")) loadPage(Integer.parseInt(action.substring(5)));
                else if (action.startsWith("play:")) {
                    closePlayer();
                    Player next = Manager.createPlayer(new MediaLocator("bd://1.PLAYLIST:" + config.getProperty("playlist")));
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
